from __future__ import annotations

import json
import uuid

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from aiogram.types import CallbackQuery, InlineQuery, Message, TelegramObject, User
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.audit import (
    create_audit_reset_marker_db,
    delete_old_audit_events_db,
    ensure_audit_table,
    fetch_audit_events_db,
    fetch_audit_events_db_window,
    fetch_audit_rows_db,
    fetch_existing_audit_request_ids_db,
    fetch_latest_audit_reset_db,
    fetch_successful_payment_rows_db,
)
from database.models import AuditEvent
from logger import logger

from .rules import (
    AUDIT_STEP_LABELS,
    DEFAULT_FUNNEL_STEPS,
    _funnel_step_counts,
    _is_ignored_analytics_event,
    _normalize_path_to_step,
    _normalize_path_to_steps,
)


try:
    from settings.cache_config import (
        AUDIT_REDIS_BUFFER_ENABLED,
        AUDIT_REDIS_DRAIN_BATCH,
        AUDIT_REDIS_FLUSH_KEY,
        AUDIT_REDIS_IDENTITY_PREFIX,
        AUDIT_REDIS_USER_PREFIX,
        AUDIT_REDIS_USER_TTL_SEC,
    )
except ImportError:
    AUDIT_REDIS_BUFFER_ENABLED = False
    AUDIT_REDIS_FLUSH_KEY = "audit:flush"
    AUDIT_REDIS_USER_PREFIX = "audit:user:tg:"
    AUDIT_REDIS_IDENTITY_PREFIX = "audit:user:identity:"
    AUDIT_REDIS_USER_TTL_SEC = 25 * 3600
    AUDIT_REDIS_DRAIN_BATCH = 1000


_MAX_TEXT_LEN = 160
_AUDIT_REDIS_PROCESSING_KEY = f"{AUDIT_REDIS_FLUSH_KEY}:processing"
_AUDIT_REDIS_DRAIN_LOCK_KEY = f"{AUDIT_REDIS_FLUSH_KEY}:drain_lock"
_AUDIT_REDIS_DRAIN_LOCK_TTL_SEC = 15 * 60


@dataclass
class AuditContext:
    request_id: str
    channel: str
    path_or_handler: str
    actor_identity_id: str | None = None
    actor_tg_id: int | None = None


def new_request_id() -> str:
    return uuid.uuid4().hex


def _naive_utc(dt: datetime) -> datetime:
    """Приводит datetime к naive UTC для запросов к колонкам DateTime (без timezone)."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def _trim(value: Any, limit: int = _MAX_TEXT_LEN) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    return _trim(value, 500)


def _serialize(payload: dict[str, Any]) -> str:
    return json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True)


async def get_audit_db_reset_at(session: AsyncSession) -> datetime | None:
    reset_at = await fetch_latest_audit_reset_db(session, source="db")
    if reset_at is None:
        return None
    if reset_at.tzinfo is None:
        return reset_at.replace(tzinfo=timezone.utc)
    return reset_at.astimezone(timezone.utc)


async def set_audit_db_reset_at(session: AsyncSession, at: datetime | None = None) -> datetime:
    created = (
        at.astimezone(timezone.utc).replace(tzinfo=None) if at is not None and at.tzinfo else (at or datetime.utcnow())
    )
    await create_audit_reset_marker_db(session, source="db", created_at=created)
    await session.commit()
    return created.replace(tzinfo=timezone.utc)


async def clear_audit_redis_buffers() -> int:
    from core.redis_cache import cache_delete_pattern

    patterns = (
        AUDIT_REDIS_FLUSH_KEY,
        _AUDIT_REDIS_PROCESSING_KEY,
        _AUDIT_REDIS_DRAIN_LOCK_KEY,
    )
    deleted = 0
    for pattern in patterns:
        deleted += await cache_delete_pattern(pattern)
    return deleted


def _message_text(event: TelegramObject) -> str | None:
    if isinstance(event, Message):
        return _trim(event.text or event.caption)
    if isinstance(event, CallbackQuery):
        return _trim(event.data)
    if isinstance(event, InlineQuery):
        return _trim(event.query)
    return None


def _event_user(event: TelegramObject) -> User | None:
    if hasattr(event, "from_user") and isinstance(event.from_user, User):
        return event.from_user
    return None


def describe_telegram_event(event: TelegramObject) -> str:
    if isinstance(event, Message):
        return f"message:{_message_text(event) or '-'}"
    if isinstance(event, CallbackQuery):
        return f"callback:{_message_text(event) or '-'}"
    if isinstance(event, InlineQuery):
        return f"inline:{_message_text(event) or '-'}"
    return type(event).__name__


def ensure_api_context(request: Request) -> AuditContext:
    context = getattr(request.state, "audit_context", None)
    if isinstance(context, AuditContext):
        return context

    path_or_handler = request.url.path
    if request.url.query:
        path_or_handler = f"{path_or_handler}?{request.url.query}"

    context = AuditContext(
        request_id=new_request_id(),
        channel="api",
        path_or_handler=path_or_handler,
    )
    request.state.audit_context = context
    request.state.audit_request_id = context.request_id
    return context


def get_api_context(request: Request | None) -> AuditContext | None:
    if request is None:
        return None
    context = getattr(request.state, "audit_context", None)
    if isinstance(context, AuditContext):
        return context
    return None


def set_api_actor(
    request: Request,
    *,
    identity_id: str | None = None,
    tg_id: int | None = None,
) -> AuditContext:
    context = ensure_api_context(request)
    if identity_id is not None:
        context.actor_identity_id = identity_id
    if tg_id is not None:
        context.actor_tg_id = tg_id
    return context


def ensure_telegram_context(
    data: dict[str, Any] | None,
    event: TelegramObject,
) -> AuditContext:
    if data is not None:
        existing = data.get("audit_context")
        if isinstance(existing, AuditContext):
            return existing

    user = _event_user(event)
    context = AuditContext(
        request_id=new_request_id(),
        channel="telegram",
        path_or_handler=describe_telegram_event(event),
        actor_tg_id=user.id if user else None,
    )
    if data is not None:
        data["audit_context"] = context
        data["audit_request_id"] = context.request_id
    return context


def set_telegram_actor(
    audit_context: AuditContext | dict[str, Any] | None,
    *,
    identity_id: str | None = None,
    tg_id: int | None = None,
) -> AuditContext | None:
    context: AuditContext | None
    if isinstance(audit_context, AuditContext):
        context = audit_context
    elif isinstance(audit_context, dict):
        context = audit_context.get("audit_context")
    else:
        context = None

    if not isinstance(context, AuditContext):
        return None
    if identity_id is not None:
        context.actor_identity_id = identity_id
    if tg_id is not None:
        context.actor_tg_id = tg_id
    return context


def get_telegram_context(audit_context: AuditContext | dict[str, Any] | None) -> AuditContext | None:
    if isinstance(audit_context, AuditContext):
        return audit_context
    if isinstance(audit_context, dict):
        context = audit_context.get("audit_context")
        if isinstance(context, AuditContext):
            return context
    return None


def _format_actor(tg_id: int | None, identity_id: str | None) -> str:
    if tg_id:
        return f"tg {tg_id}"
    if identity_id:
        return f"id {str(identity_id)[:8]}"
    return "anon"


def log_api_access(
    request: Request,
    *,
    status_code: int,
    duration_ms: int,
    result: str,
    reason: str | None = None,
) -> None:
    context = ensure_api_context(request)
    client_ip = request.client.host if request.client else "-"
    actor = _format_actor(context.actor_tg_id, context.actor_identity_id)
    line = (
        f"[API] {request.method} {context.path_or_handler} → {status_code} {result} │ "
        f"{duration_ms}ms │ ip {client_ip} │ {actor} │ req {str(context.request_id)[:8]}"
    )
    if reason:
        line += f" │ {reason}"
    logger.debug(line)


def log_telegram_access(
    event: TelegramObject,
    *,
    audit_context: AuditContext | None,
    result: str,
    reason: str | None = None,
) -> None:
    context = audit_context or AuditContext(
        request_id=new_request_id(),
        channel="telegram",
        path_or_handler=describe_telegram_event(event),
    )
    user = _event_user(event)
    tg_id = context.actor_tg_id or (user.id if user else None)
    username = getattr(user, "username", None) if user else None
    actor = f"tg {tg_id}" if tg_id else "anon"
    if username:
        actor += f" @{username}"
    line = f"[TG] {context.path_or_handler} → {result} │ {actor} │ req {str(context.request_id)[:8]}"
    message_text = _message_text(event)
    if message_text:
        line += f" │ «{message_text}»"
    if reason:
        line += f" │ {reason}"
    logger.debug(line)


async def record_audit_event(
    session: AsyncSession,
    *,
    event_type: str,
    channel: str,
    path_or_handler: str,
    actor_identity_id: str | None = None,
    actor_tg_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    result: str = "success",
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditEvent:
    await ensure_audit_table(session)
    event = AuditEvent(
        event_type=event_type,
        channel=channel,
        actor_identity_id=actor_identity_id,
        actor_tg_id=actor_tg_id,
        path_or_handler=_trim(path_or_handler, 255) or channel,
        entity_type=_trim(entity_type, 64),
        entity_id=_trim(entity_id, 255),
        result=_trim(result, 32) or "success",
        reason=_trim(reason, 1000),
        metadata_=_jsonable(metadata) if metadata else None,
        request_id=_trim(request_id, 64),
    )
    session.add(event)
    await session.flush()
    logger.debug(
        f"[AUDIT_EVENT] {
            _serialize({
                'id': event.id,
                'request_id': event.request_id,
                'channel': event.channel,
                'event_type': event.event_type,
                'actor_identity_id': event.actor_identity_id,
                'actor_tg_id': event.actor_tg_id,
                'path_or_handler': event.path_or_handler,
                'entity_type': event.entity_type,
                'entity_id': event.entity_id,
                'result': event.result,
                'reason': event.reason,
                'metadata': event.metadata_,
            })
        }"
    )
    return event


async def safe_record_audit_event(session: AsyncSession, **kwargs: Any) -> AuditEvent | None:
    try:
        return await record_audit_event(session, **kwargs)
    except Exception as exc:
        logger.warning(f"[Audit] Не удалось записать событие {kwargs.get('event_type')}: {exc}")
        return None


def _telegram_access_payload(
    audit_context: AuditContext | None,
    event: TelegramObject,
    *,
    result: str = "success",
    reason: str | None = None,
) -> dict[str, Any]:
    ctx = get_telegram_context(audit_context)
    user = _event_user(event)
    path = describe_telegram_event(event)
    if ctx is None:
        ctx = AuditContext(
            request_id=new_request_id(),
            channel="telegram",
            path_or_handler=path,
            actor_tg_id=user.id if user else None,
        )
    return {
        "request_id": ctx.request_id,
        "path_or_handler": path,
        "actor_identity_id": ctx.actor_identity_id,
        "actor_tg_id": ctx.actor_tg_id or (user.id if user else None),
        "result": result,
        "reason": reason,
    }


async def record_telegram_access_event(
    session: AsyncSession,
    audit_context: AuditContext | None,
    event: TelegramObject,
    *,
    result: str = "success",
    reason: str | None = None,
) -> AuditEvent | None:
    payload = _telegram_access_payload(audit_context, event, result=result, reason=reason)
    return await safe_record_audit_event(
        session,
        event_type="telegram_access",
        channel="telegram",
        path_or_handler=payload["path_or_handler"],
        actor_identity_id=payload["actor_identity_id"],
        actor_tg_id=payload["actor_tg_id"],
        result=payload["result"],
        reason=payload["reason"],
        request_id=payload["request_id"],
    )


def _audit_record_for_redis(
    *,
    event_type: str = "telegram_access",
    channel: str = "telegram",
    path_or_handler: str,
    actor_identity_id: str | None = None,
    actor_tg_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    result: str = "success",
    reason: str | None = None,
    request_id: str | None = None,
    metadata_: dict | None = None,
) -> dict[str, Any]:
    effective_request_id = _trim(request_id, 64) if request_id else new_request_id()
    return {
        "event_type": event_type,
        "channel": channel,
        "path_or_handler": _trim(path_or_handler, 255) or channel,
        "actor_identity_id": actor_identity_id,
        "actor_tg_id": actor_tg_id,
        "entity_type": _trim(entity_type, 64) if entity_type else None,
        "entity_id": _trim(str(entity_id), 255) if entity_id is not None else None,
        "result": _trim(result, 32) or "success",
        "reason": _trim(reason, 1000) if reason else None,
        "request_id": effective_request_id,
        "metadata_": _jsonable(metadata_) if metadata_ else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def _push_audit_record_to_redis(record: dict[str, Any]) -> None:
    from core.redis_cache import cache_expire, cache_rpush

    n = await cache_rpush(AUDIT_REDIS_FLUSH_KEY, record)
    if n == 0:
        raise RuntimeError("Redis unavailable (cache_rpush returned 0)")

    actor_tg_id = record.get("actor_tg_id")
    actor_identity_id = record.get("actor_identity_id")
    if actor_tg_id is not None:
        user_key = f"{AUDIT_REDIS_USER_PREFIX}{actor_tg_id}"
        await cache_rpush(user_key, record)
        await cache_expire(user_key, AUDIT_REDIS_USER_TTL_SEC)
    if actor_identity_id:
        identity_key = f"{AUDIT_REDIS_IDENTITY_PREFIX}{actor_identity_id}"
        await cache_rpush(identity_key, record)
        await cache_expire(identity_key, AUDIT_REDIS_USER_TTL_SEC)


async def record_audit_event_to_redis(
    *,
    request_id: str | None = None,
    path_or_handler: str = "",
    actor_identity_id: str | None = None,
    actor_tg_id: int | None = None,
    result: str = "success",
    reason: str | None = None,
) -> None:
    record = _audit_record_for_redis(
        path_or_handler=path_or_handler,
        actor_identity_id=actor_identity_id,
        actor_tg_id=actor_tg_id,
        result=result,
        reason=reason,
        request_id=request_id,
    )
    await _push_audit_record_to_redis(record)


async def record_api_access_event_to_redis(
    *,
    request_id: str | None = None,
    path_or_handler: str = "",
    actor_identity_id: str | None = None,
    actor_tg_id: int | None = None,
    result: str = "success",
    reason: str | None = None,
) -> None:
    record = _audit_record_for_redis(
        event_type="api_access",
        channel="api",
        path_or_handler=path_or_handler,
        actor_identity_id=actor_identity_id,
        actor_tg_id=actor_tg_id,
        result=result,
        reason=reason,
        request_id=request_id,
    )
    await _push_audit_record_to_redis(record)


async def record_api_access_event_background(
    session_factory: Any,
    request: Request,
    *,
    result: str = "success",
    reason: str | None = None,
    status_code: int = 200,
) -> None:
    context = ensure_api_context(request)
    path_or_handler = f"{request.method} {request.url.path}"
    if request.url.query:
        path_or_handler = f"{path_or_handler}?{request.url.query}"
    path_or_handler = _trim(path_or_handler, 255) or "api"
    if AUDIT_REDIS_BUFFER_ENABLED:
        try:
            await record_api_access_event_to_redis(
                request_id=context.request_id,
                path_or_handler=path_or_handler,
                actor_identity_id=context.actor_identity_id,
                actor_tg_id=context.actor_tg_id,
                result=result,
                reason=reason,
            )
        except Exception as exc:
            logger.warning("[Audit] Запись api_access в Redis-буфер не удалась: {}", exc)
        return
    try:
        async with session_factory() as session:
            await ensure_audit_table(session)
            await record_audit_event(
                session,
                event_type="api_access",
                channel="api",
                path_or_handler=path_or_handler,
                actor_identity_id=context.actor_identity_id,
                actor_tg_id=context.actor_tg_id,
                result=result,
                reason=reason,
                request_id=_trim(context.request_id, 64),
            )
            await session.commit()
    except Exception as exc:
        logger.warning(
            "[Audit] Фоновая запись api_access не удалась: %s",
            exc,
            extra={"path_or_handler": path_or_handler[:80] if path_or_handler else None},
        )


async def record_telegram_access_event_background(
    session_factory: Any,
    *,
    request_id: str | None,
    path_or_handler: str,
    actor_identity_id: str | None = None,
    actor_tg_id: int | None = None,
    result: str = "success",
    reason: str | None = None,
) -> None:
    if AUDIT_REDIS_BUFFER_ENABLED:
        try:
            await record_audit_event_to_redis(
                request_id=request_id,
                path_or_handler=path_or_handler,
                actor_identity_id=actor_identity_id,
                actor_tg_id=actor_tg_id,
                result=result,
                reason=reason,
            )
            return
        except Exception as exc:
            logger.warning(f"[Audit] Запись в Redis-буфер не удалась, пишем в БД: {exc}")
    try:
        async with session_factory() as session:
            await ensure_audit_table(session)
            await record_audit_event(
                session,
                event_type="telegram_access",
                channel="telegram",
                path_or_handler=_trim(path_or_handler, 255) or "telegram",
                actor_identity_id=actor_identity_id,
                actor_tg_id=actor_tg_id,
                result=result,
                reason=reason,
                request_id=_trim(request_id, 64),
            )
            await session.commit()
    except Exception as exc:
        logger.warning(
            "[Audit] Фоновая запись telegram_access не удалась: %s",
            exc,
            extra={"path_or_handler": path_or_handler[:80] if path_or_handler else None},
        )


async def safe_record_api_event(
    session: AsyncSession,
    request: Request,
    *,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    result: str = "success",
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_identity_id: str | None = None,
    actor_tg_id: int | None = None,
    path_or_handler: str | None = None,
) -> AuditEvent | None:
    context = ensure_api_context(request)
    return await safe_record_audit_event(
        session,
        event_type=event_type,
        channel="api",
        path_or_handler=path_or_handler or context.path_or_handler,
        actor_identity_id=actor_identity_id if actor_identity_id is not None else context.actor_identity_id,
        actor_tg_id=actor_tg_id if actor_tg_id is not None else context.actor_tg_id,
        entity_type=entity_type,
        entity_id=entity_id,
        result=result,
        reason=reason,
        metadata=metadata,
        request_id=context.request_id,
    )


async def safe_record_telegram_event(
    session: AsyncSession,
    audit_context: AuditContext | dict[str, Any] | None,
    *,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    result: str = "success",
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor_identity_id: str | None = None,
    actor_tg_id: int | None = None,
    path_or_handler: str | None = None,
) -> AuditEvent | None:
    context = get_telegram_context(audit_context)
    return await safe_record_audit_event(
        session,
        event_type=event_type,
        channel="telegram",
        path_or_handler=path_or_handler or (context.path_or_handler if context else "telegram"),
        actor_identity_id=actor_identity_id
        if actor_identity_id is not None
        else (context.actor_identity_id if context else None),
        actor_tg_id=actor_tg_id if actor_tg_id is not None else (context.actor_tg_id if context else None),
        entity_type=entity_type,
        entity_id=entity_id,
        result=result,
        reason=reason,
        metadata=metadata,
        request_id=context.request_id if context else None,
    )


def _redis_record_to_event_like(rec: dict[str, Any]) -> SimpleNamespace:
    created = rec.get("created_at")
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except Exception:
            created = datetime.now(timezone.utc)
    elif created is None:
        created = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=None,
        event_type=rec.get("event_type", "telegram_access"),
        channel=rec.get("channel", "telegram"),
        path_or_handler=rec.get("path_or_handler") or "",
        actor_identity_id=rec.get("actor_identity_id"),
        actor_tg_id=rec.get("actor_tg_id"),
        entity_type=rec.get("entity_type"),
        entity_id=rec.get("entity_id"),
        result=rec.get("result", "success"),
        reason=rec.get("reason"),
        metadata_=rec.get("metadata_"),
        request_id=rec.get("request_id"),
        created_at=created,
    )


async def _list_audit_events_from_redis(
    tg_id: int | None,
    identity_id: str | None,
    channel: str | None,
    event_types: list[str] | None,
    max_events: int = 3000,
) -> list[SimpleNamespace]:
    if not AUDIT_REDIS_BUFFER_ENABLED:
        return []
    from core.redis_cache import cache_lrange

    out: list[SimpleNamespace] = []
    seen: set[tuple[str, str]] = set()
    keys_to_read = []
    if tg_id is not None:
        keys_to_read.append(f"{AUDIT_REDIS_USER_PREFIX}{tg_id}")
    if identity_id:
        keys_to_read.append(f"{AUDIT_REDIS_IDENTITY_PREFIX}{identity_id}")
    for key in keys_to_read:
        raw = await cache_lrange(key, -max_events, -1)
        for rec in reversed(raw):
            if not isinstance(rec, dict):
                continue
            created = rec.get("created_at")
            rid = rec.get("request_id") or ""
            if (created, rid) in seen:
                continue
            if channel and rec.get("channel") != channel:
                continue
            if event_types and rec.get("event_type") not in event_types:
                continue
            seen.add((str(created), rid))
            out.append(_redis_record_to_event_like(rec))
    out.sort(key=lambda e: e.created_at, reverse=True)
    return out[:max_events]


async def list_audit_events_from_redis_buffer(max_events: int = 5000) -> list[SimpleNamespace]:
    if not AUDIT_REDIS_BUFFER_ENABLED:
        return []
    from core.redis_cache import cache_lrange

    raw = await cache_lrange(AUDIT_REDIS_FLUSH_KEY, -max_events, -1)
    out = []
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        out.append(_redis_record_to_event_like(rec))
    out.sort(key=lambda e: e.created_at)
    return out


def _aggregate_audit_rows(
    rows: list[tuple[Any, Any, Any, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], set[tuple[str, int | str]]]:
    by_step: dict[str, dict[str, Any]] = {}
    all_actors: set[tuple[str, int | str]] = set()

    for row in rows:
        path, res, tg_id, identity_id = row[0], row[1], row[2], row[3]
        if _is_ignored_analytics_event(path or ""):
            continue
        actor = _audit_actor_key(identity_id, tg_id)
        if actor is not None:
            all_actors.add(actor)
        for step in _normalize_path_to_steps(path or ""):
            if step not in by_step:
                by_step[step] = {"total": 0, "success": 0, "fail": 0, "actors": set()}
            by_step[step]["total"] += 1
            if res == "success":
                by_step[step]["success"] += 1
            else:
                by_step[step]["fail"] += 1
            if actor is not None:
                by_step[step]["actors"].add(actor)

    by_path_list = []
    for step, data in sorted(by_step.items(), key=lambda x: -x[1]["total"]):
        total = data["total"]
        fail = data["fail"]
        unique = len(data["actors"])
        fail_rate = round(100.0 * fail / total, 1) if total else 0
        by_path_list.append({
            "step": step,
            "label": AUDIT_STEP_LABELS.get(step, step),
            "total": total,
            "success": data["success"],
            "fail": fail,
            "unique_users": unique,
            "fail_rate_pct": fail_rate,
        })
    return by_step, by_path_list, all_actors


def _audit_actor_key(identity_id: Any, tg_id: Any) -> tuple[str, str | int] | None:
    if tg_id not in (None, 0, "0", ""):
        try:
            return ("tg", int(tg_id))
        except (TypeError, ValueError):
            return ("tg", str(tg_id))
    if identity_id not in (None, ""):
        return ("identity", str(identity_id))
    return None


def _event_like_dedupe_key(event: AuditEvent | SimpleNamespace) -> tuple[Any, ...]:
    request_id = getattr(event, "request_id", None)
    event_type = getattr(event, "event_type", None)
    path = getattr(event, "path_or_handler", None)
    result = getattr(event, "result", None)
    if request_id:
        return ("request", request_id, event_type, path, result)
    return (
        "raw",
        event_type,
        getattr(event, "channel", None),
        path,
        getattr(event, "actor_tg_id", None),
        getattr(event, "actor_identity_id", None),
        result,
        getattr(event, "reason", None),
        str(getattr(event, "created_at", None)),
    )


def _dedupe_event_like(events: list[AuditEvent | SimpleNamespace]) -> list[AuditEvent | SimpleNamespace]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[AuditEvent | SimpleNamespace] = []
    for event in events:
        key = _event_like_dedupe_key(event)
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


async def list_audit_events(
    session: AsyncSession,
    *,
    identity_id: str | None = None,
    tg_id: int | None = None,
    channel: str | None = None,
    event_type: str | None = None,
    event_types: Iterable[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEvent | SimpleNamespace]:
    event_types_list = sorted(event_types) if event_types else None

    if not AUDIT_REDIS_BUFFER_ENABLED:
        return await fetch_audit_events_db(
            session,
            identity_id=identity_id,
            tg_id=tg_id,
            channel=channel,
            event_type=event_type,
            event_types=event_types_list,
            limit=limit,
            offset=offset,
        )

    redis_events = await _list_audit_events_from_redis(tg_id, identity_id, channel, event_types_list, max_events=3000)
    need = offset + limit + len(redis_events)
    db_events = await fetch_audit_events_db_window(
        session,
        identity_id=identity_id,
        tg_id=tg_id,
        channel=channel,
        event_type=event_type,
        event_types=event_types_list,
        limit=min(5000, need),
    )
    merged = _dedupe_event_like(redis_events + db_events)
    for ev in merged:
        if ev.created_at is not None and ev.created_at.tzinfo is None:
            ev.created_at = ev.created_at.replace(tzinfo=timezone.utc)
    merged.sort(key=lambda e: (e.created_at, getattr(e, "id", 0)), reverse=True)
    return merged[offset : offset + limit]


async def delete_old_audit_events(
    session: AsyncSession,
    *,
    older_than_days: int = 90,
) -> int:
    return await delete_old_audit_events_db(session, older_than_days=older_than_days)


async def get_audit_stats(
    session: AsyncSession,
    *,
    date_from: datetime,
    date_to: datetime,
    max_events: int = 100_000,
) -> dict[str, Any]:
    d_from = _naive_utc(date_from)
    d_to = _naive_utc(date_to)
    rows = await fetch_audit_rows_db(session, date_from=d_from, date_to=d_to, limit=max_events)
    rows.extend(await fetch_successful_payment_rows_db(session, date_from=d_from, date_to=d_to, limit=max_events))
    _by_step, by_path_list, all_actors = _aggregate_audit_rows(rows)
    raw_total_events = sum(1 for row in rows if not _is_ignored_analytics_event(row[0] or ""))
    analytics_total_events = sum(row["total"] for row in by_path_list)
    return {
        "summary": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "total_events": raw_total_events,
            "raw_total_events": raw_total_events,
            "analytics_total_events": analytics_total_events,
            "unique_users": len(all_actors),
        },
        "by_path": by_path_list,
    }


async def get_audit_stats_from_redis(max_events: int = 5000) -> dict[str, Any] | None:
    if not AUDIT_REDIS_BUFFER_ENABLED:
        return None
    events = await list_audit_events_from_redis_buffer(max_events=max_events)
    filtered_events = events
    rows = [(e.path_or_handler, e.result, e.actor_tg_id, e.actor_identity_id) for e in filtered_events]
    _by_step, by_path_list, all_actors = _aggregate_audit_rows(rows)
    raw_total_events = sum(1 for row in rows if not _is_ignored_analytics_event(row[0] or ""))
    analytics_total_events = sum(row["total"] for row in by_path_list)
    return {
        "summary": {
            "source": "redis",
            "total_events": raw_total_events,
            "raw_total_events": raw_total_events,
            "analytics_total_events": analytics_total_events,
            "unique_users": len(all_actors),
        },
        "by_path": by_path_list,
    }


async def get_audit_stats_from_redis_since(
    *,
    date_from: datetime | None = None,
    max_events: int = 5000,
) -> dict[str, Any] | None:
    if not AUDIT_REDIS_BUFFER_ENABLED:
        return None
    events = await list_audit_events_from_redis_buffer(max_events=max_events)
    filtered_events = events
    if date_from is not None:
        threshold = date_from.astimezone(timezone.utc) if date_from.tzinfo else date_from.replace(tzinfo=timezone.utc)
        filtered_events = [e for e in events if getattr(e, "created_at", None) and e.created_at >= threshold]
    rows = [(e.path_or_handler, e.result, e.actor_tg_id, e.actor_identity_id) for e in filtered_events]
    _by_step, by_path_list, all_actors = _aggregate_audit_rows(rows)
    raw_total_events = sum(1 for row in rows if not _is_ignored_analytics_event(row[0] or ""))
    analytics_total_events = sum(row["total"] for row in by_path_list)
    return {
        "summary": {
            "source": "redis",
            "total_events": raw_total_events,
            "raw_total_events": raw_total_events,
            "analytics_total_events": analytics_total_events,
            "unique_users": len(all_actors),
        },
        "by_path": by_path_list,
    }


async def get_audit_funnel(
    session: AsyncSession,
    *,
    date_from: datetime,
    date_to: datetime,
    steps_ordered: tuple[str, ...] | None = None,
    max_events: int = 50_000,
) -> list[dict[str, Any]]:
    steps = steps_ordered or DEFAULT_FUNNEL_STEPS
    d_from = _naive_utc(date_from)
    d_to = _naive_utc(date_to)
    rows = await fetch_audit_rows_db(session, date_from=d_from, date_to=d_to, limit=max_events)
    rows.extend(await fetch_successful_payment_rows_db(session, date_from=d_from, date_to=d_to, limit=max_events))
    return _funnel_from_rows(rows, steps)


def _funnel_from_rows(
    rows: list[tuple[Any, ...]],
    steps_ordered: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    steps = steps_ordered or DEFAULT_FUNNEL_STEPS
    step_actors: dict[str, set[tuple[str, str | int]]] = {step: set() for step in steps}
    for row in rows:
        if len(row) >= 4:
            path, result, tg_id, identity_id = row[0], row[1], row[2], row[3]
        else:
            path, tg_id, identity_id = row[0], row[1], row[2]
            result = "success"
        if _is_ignored_analytics_event(path or ""):
            continue
        key = _audit_actor_key(identity_id, tg_id)
        if key is None:
            continue
        for step in _normalize_path_to_steps(path or ""):
            if not _funnel_step_counts(path or "", str(result or ""), step):
                continue
            if step in step_actors:
                step_actors[step].add(key)

    funnel_list = []
    prev_actors: set[tuple[str, str | int]] | None = None
    for step in steps:
        actors = step_actors.get(step, set())
        count = len(actors)
        conversion = None
        if prev_actors is not None and prev_actors:
            overlap = len(prev_actors & actors)
            conversion = round(100.0 * overlap / len(prev_actors), 1)
        funnel_list.append({
            "step": step,
            "label": AUDIT_STEP_LABELS.get(step, step),
            "count": count,
            "conversion_from_prev_pct": conversion,
        })
        prev_actors = actors
    return funnel_list


async def get_audit_funnel_from_redis(
    max_events: int = 5000,
    steps_ordered: tuple[str, ...] | None = None,
) -> list[dict[str, Any]] | None:
    if not AUDIT_REDIS_BUFFER_ENABLED:
        return None
    events = await list_audit_events_from_redis_buffer(max_events=max_events)
    rows = [(e.path_or_handler, e.result, e.actor_tg_id, e.actor_identity_id) for e in events]
    return _funnel_from_rows(rows, steps_ordered)


async def get_audit_funnel_from_redis_since(
    *,
    date_from: datetime | None = None,
    max_events: int = 5000,
    steps_ordered: tuple[str, ...] | None = None,
) -> list[dict[str, Any]] | None:
    if not AUDIT_REDIS_BUFFER_ENABLED:
        return None
    events = await list_audit_events_from_redis_buffer(max_events=max_events)
    filtered_events = events
    if date_from is not None:
        threshold = date_from.astimezone(timezone.utc) if date_from.tzinfo else date_from.replace(tzinfo=timezone.utc)
        filtered_events = [e for e in events if getattr(e, "created_at", None) and e.created_at >= threshold]
    rows = [(e.path_or_handler, e.result, e.actor_tg_id, e.actor_identity_id) for e in filtered_events]
    return _funnel_from_rows(rows, steps_ordered)


async def drain_audit_redis_to_db(session_factory: Any) -> int:
    from core.redis_cache import cache_delete, cache_lmove_batch, cache_lpop_batch, cache_lrange, cache_setnx

    if not await cache_setnx(_AUDIT_REDIS_DRAIN_LOCK_KEY, 1, _AUDIT_REDIS_DRAIN_LOCK_TTL_SEC):
        logger.info("[Audit] drain_audit_redis_to_db пропущен: уже выполняется другой drain")
        return 0
    total = 0
    try:
        while True:
            raw_batch = await cache_lrange(_AUDIT_REDIS_PROCESSING_KEY, 0, AUDIT_REDIS_DRAIN_BATCH - 1)
            if not raw_batch:
                raw_batch = await cache_lmove_batch(
                    AUDIT_REDIS_FLUSH_KEY,
                    _AUDIT_REDIS_PROCESSING_KEY,
                    AUDIT_REDIS_DRAIN_BATCH,
                )
            if not raw_batch:
                break
            batch = [rec for rec in raw_batch if isinstance(rec, dict)]
            if not batch:
                logger.warning(
                    "[Audit] drain_audit_redis_to_db: отброшен пустой/битый батч ({} элементов)", len(raw_batch)
                )
                await cache_lpop_batch(_AUDIT_REDIS_PROCESSING_KEY, len(raw_batch))
                continue
            try:
                async with session_factory() as session:
                    await ensure_audit_table(session)
                    existing_request_ids = await fetch_existing_audit_request_ids_db(
                        session,
                        [str(rec.get("request_id")) for rec in batch if rec.get("request_id")],
                    )
                    identity_ids_in_batch = {
                        str(rec.get("actor_identity_id")) for rec in batch if rec.get("actor_identity_id")
                    }
                    if identity_ids_in_batch:
                        from database.models import Identity

                        rows = await session.execute(select(Identity.id).where(Identity.id.in_(identity_ids_in_batch)))
                        existing_identity_ids = {row[0] for row in rows.all()}
                    else:
                        existing_identity_ids = set()
                    inserted_count = 0
                    seen_batch_request_ids: set[str] = set()
                    for rec in batch:
                        request_id = rec.get("request_id")
                        if request_id and (request_id in existing_request_ids or request_id in seen_batch_request_ids):
                            continue
                        if request_id:
                            seen_batch_request_ids.add(request_id)
                        actor_identity_id = rec.get("actor_identity_id")
                        if actor_identity_id and actor_identity_id not in existing_identity_ids:
                            actor_identity_id = None
                        created = rec.get("created_at")
                        if isinstance(created, str):
                            try:
                                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            except Exception:
                                created = datetime.now(timezone.utc)
                        elif created is None:
                            created = datetime.now(timezone.utc)
                        elif not isinstance(created, datetime):
                            created = datetime.now(timezone.utc)
                        created = _naive_utc(created)
                        event = AuditEvent(
                            event_type=rec.get("event_type", "telegram_access"),
                            channel=rec.get("channel", "telegram"),
                            path_or_handler=rec.get("path_or_handler") or "telegram",
                            actor_identity_id=actor_identity_id,
                            actor_tg_id=rec.get("actor_tg_id"),
                            entity_type=rec.get("entity_type"),
                            entity_id=rec.get("entity_id"),
                            result=rec.get("result", "success"),
                            reason=rec.get("reason"),
                            metadata_=rec.get("metadata_"),
                            request_id=request_id,
                            created_at=created,
                        )
                        session.add(event)
                        inserted_count += 1
                    await session.commit()
                await cache_lpop_batch(_AUDIT_REDIS_PROCESSING_KEY, len(raw_batch))
                total += inserted_count
            except Exception as exc:
                logger.warning("[Audit] drain_audit_redis_to_db батч не записан, останется в Redis processing: {}", exc)
                break
        return total
    finally:
        await cache_delete(_AUDIT_REDIS_DRAIN_LOCK_KEY)
