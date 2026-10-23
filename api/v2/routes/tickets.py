import asyncio
import uuid as uuid_mod

from datetime import UTC, datetime
from pathlib import Path as FsPath

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    get_session,
    resolve_identity_role,
    verify_identity_admin,
    verify_identity_agent,
    verify_identity_token,
)
from core.executor import run_io
from core.settings.modes_config import MODES_CONFIG
from database import async_session_maker
from database.models import Admin, Identity, Ticket, TicketDelivery, TicketMessage, TicketTelegramMessage, User
from logger import logger
from services import tickets as svc
from services.tickets.events import TICKETS_EVENTS_CHANNEL, publish_tickets_changed, tickets_client_channel
from services.tickets.messages import delete_message, edit_message, message_permissions
from services.tickets.telegram import sync_message
from settings.config import REDIS_URL
from settings.texts import SUPPORT_MESSAGE_DELIVERY_FAILED


async def require_tickets_enabled() -> None:
    if not MODES_CONFIG.get("SUPPORT_TICKETS_ENABLED", False):
        raise HTTPException(status_code=404, detail="Система тикетов отключена")


router = APIRouter()
admin_router = APIRouter()

MAX_BODY = 4000
MAX_ATTACHMENTS = 10
_MESSAGE_DELIVERY_KINDS = ("telegram_forum", "telegram_client", "telegram_agent", "telegram_sync", "telegram_notice")


class CreateTicketBody(BaseModel):
    body: str
    category: str | None = None
    subject: str | None = None
    attachments: list[str] | None = None
    ref_type: str | None = None
    ref_id: str | None = None


class MessageBody(BaseModel):
    body: str = ""
    attachments: list[str] | None = None
    internal: bool = False


class EditMessageBody(BaseModel):
    body: str = Field(max_length=MAX_BODY)
    revision: int | None = Field(default=None, ge=0)
    attachments: list[str] | None = None


class AgentPatchBody(BaseModel):
    status: str | None = None
    assigned_agent_tg_id: int | None = None
    unassign: bool = False
    priority: str | None = None
    tags: list[str] | None = None


class RatingBody(BaseModel):
    rating: int


_ATTACHMENT_URL_PREFIX = "/api/web/uploads/"


def _clean_attachments(items: list[str] | None) -> list[str] | None:
    if not items:
        return None
    out = []
    for x in items:
        if not isinstance(x, str):
            continue
        url = x.strip()
        if not url.startswith(_ATTACHMENT_URL_PREFIX):
            continue
        name = url[len(_ATTACHMENT_URL_PREFIX) :]
        if not name or "/" in name or ".." in name:
            continue
        out.append(url)
    return out[:MAX_ATTACHMENTS] or None


def _iso(dt) -> str | None:
    """ISO-время всегда с UTC-смещением: naive-значения (колонки без tz) считаем UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _delivery_summary(message: TicketMessage, jobs: list[TicketDelivery]) -> dict:
    """Скрывает технические ошибки и учитывает текущую версию сообщения."""
    statuses = [
        row.status
        for row in jobs
        if (row.payload or {}).get("revision") == message.revision and not (row.payload or {}).get("skipped")
    ]
    status = None
    if any(value in {"pending", "running", "retry"} for value in statuses):
        status = "pending"
    elif "failed" in statuses:
        status = "failed"
    elif statuses and all(value == "done" for value in statuses):
        status = "delivered"
    return {
        "delivery_status": status,
        "delivery_error": SUPPORT_MESSAGE_DELIVERY_FAILED if status == "failed" else None,
    }


def _msg_dict(m: TicketMessage, permissions: dict | None = None, delivery: dict | None = None) -> dict:
    deleted = m.deleted_at is not None
    return {
        "id": m.id,
        "author": m.author,
        "agent_tg_id": m.agent_tg_id,
        "body": "" if deleted else m.body,
        "attachments": [] if deleted else (m.attachments or []),
        "created_at": _iso(m.created_at),
        "read_by_client": m.read_by_client,
        "read_by_agent": m.read_by_agent,
        "edited_at": _iso(m.edited_at),
        "deleted": deleted,
        "revision": m.revision,
        "can_edit": bool((permissions or {}).get("can_edit")) and not deleted,
        "can_delete": bool((permissions or {}).get("can_delete")) and not deleted,
        "delivery_status": (delivery or {}).get("delivery_status"),
        "delivery_error": (delivery or {}).get("delivery_error"),
    }


async def _message_dicts(session, ticket: Ticket, messages: list[TicketMessage], identity, role: str) -> list[dict]:
    """Добавляет права и состояния доставки общей пакетной выборкой."""
    if not messages:
        return []
    message_ids = [m.id for m in messages]
    source_ids = set(
        (
            await session.scalars(
                select(TicketTelegramMessage.ticket_message_id).where(
                    TicketTelegramMessage.ticket_message_id.in_(message_ids),
                    TicketTelegramMessage.source.is_(True),
                )
            )
        ).all()
    )
    delivery_jobs = list(
        (
            await session.scalars(
                select(TicketDelivery)
                .where(
                    TicketDelivery.ticket_id == ticket.id,
                    TicketDelivery.ticket_message_id.in_(message_ids),
                    TicketDelivery.kind.in_(_MESSAGE_DELIVERY_KINDS),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    by_message = {message_id: [] for message_id in message_ids}
    for row in delivery_jobs:
        by_message[row.ticket_message_id].append(row)
    result = []
    for msg in messages:
        permissions = message_permissions(
            ticket,
            msg,
            identity_id=identity.id,
            tg_id=getattr(identity, "tg_id", None),
            role=role,
        )
        if msg.id in source_ids:
            permissions = {**permissions, "can_edit": False}
        result.append(_msg_dict(msg, permissions, _delivery_summary(msg, by_message[msg.id])))
    return result


async def _mutable_message(session, ticket: Ticket, message_id: str, identity, role: str, action: str):
    """Проверяет принадлежность сообщения и право изменения."""
    ticket = await session.scalar(
        select(Ticket).where(Ticket.id == ticket.id).with_for_update().execution_options(populate_existing=True)
    )
    if ticket is None or (role == "user" and ticket.identity_id != identity.id):
        raise HTTPException(status_code=404, detail="Тикет не найден")
    msg = await session.scalar(
        select(TicketMessage)
        .where(TicketMessage.id == message_id, TicketMessage.ticket_id == ticket.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if msg is None or msg.ticket_id != ticket.id or (role == "user" and msg.author == "note"):
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    data = (await _message_dicts(session, ticket, [msg], identity, role))[0]
    if not data[f"can_{action}"]:
        raise HTTPException(status_code=403, detail="Изменение этого сообщения недоступно")
    return msg


async def _edit_ticket_message(session, ticket: Ticket, message_id: str, payload: EditMessageBody, identity, role: str):
    """Изменяет доступное сообщение и его Telegram-копии."""
    previous = await _mutable_message(session, ticket, message_id, identity, role, "edit")
    body = payload.body.strip()
    attachments = (_clean_attachments(payload.attachments) or []) if payload.attachments is not None else None
    effective_attachments = previous.attachments if payload.attachments is None else attachments
    if not body and not effective_attachments:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    try:
        msg = await edit_message(
            session,
            ticket_id=ticket.id,
            message_id=message_id,
            body=body,
            attachments=attachments,
            expected_revision=payload.revision,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="Сообщение уже изменилось. Обновите переписку и повторите."
        ) from exc
    if msg is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    await sync_message(session, msg)
    return (await _message_dicts(session, ticket, [msg], identity, role))[0]


async def _delete_ticket_message(session, ticket: Ticket, message_id: str, revision: int | None, identity, role: str):
    """Удаляет доступное сообщение и его Telegram-копии."""
    await _mutable_message(session, ticket, message_id, identity, role, "delete")
    try:
        msg = await delete_message(session, ticket_id=ticket.id, message_id=message_id, expected_revision=revision)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="Сообщение уже изменилось. Обновите переписку и повторите."
        ) from exc
    if msg is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    await sync_message(session, msg)
    return (await _message_dicts(session, ticket, [msg], identity, role))[0]


def _ticket_dict(t: Ticket) -> dict:
    return {
        "id": t.id,
        "identity_id": t.identity_id,
        "subject": t.subject,
        "category": t.category,
        "priority": t.priority,
        "status": t.status,
        "source": t.source,
        "assigned_agent_tg_id": t.assigned_agent_tg_id,
        "rating": t.rating,
        "tags": t.tags or [],
        "ref_type": t.ref_type,
        "ref_id": t.ref_id,
        "created_at": _iso(t.created_at),
        "updated_at": _iso(t.updated_at),
        "last_message_at": _iso(t.last_message_at),
    }


_UPLOAD_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_UPLOAD_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@router.post("/tickets/upload")
async def upload_ticket_attachment(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    from api.ratelimit import enforce_rate_limit
    from utils.web_media import MAX_UPLOAD_BYTES, WEB_UPLOAD_DIR

    await enforce_rate_limit(request, session, bucket="ticket_upload", max_per_window=20, window_sec=60)
    if not file.filename or "." not in file.filename:
        raise HTTPException(status_code=400, detail="Файл должен иметь расширение")
    ext = FsPath(file.filename).suffix.lower()
    if ext not in _UPLOAD_IMAGE_EXT:
        raise HTTPException(
            status_code=400, detail=f"Разрешены только картинки: {', '.join(sorted(_UPLOAD_IMAGE_EXT))}"
        )
    if file.content_type and file.content_type.lower() not in _UPLOAD_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Файл не похож на картинку")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"Размер не более {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
    if ext != ".gif":
        try:
            from core.executor import run_cpu
            from utils.cpu_tasks import optimize_image_bytes

            data = await run_cpu(optimize_image_bytes, data, ext)
        except Exception:
            pass
    WEB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid_mod.uuid4().hex}{ext}"
    await run_io((WEB_UPLOAD_DIR / name).write_bytes, data)
    return {"url": f"{_ATTACHMENT_URL_PREFIX}{name}"}


@router.post("/tickets")
async def create_ticket(
    payload: CreateTicketBody,
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Опишите проблему")
    try:
        ticket = await svc.create_ticket(
            session,
            identity_id=identity.id,
            body=body[:MAX_BODY],
            category=payload.category,
            subject=payload.subject,
            source="web",
            attachments=_clean_attachments(payload.attachments),
            ref_type=payload.ref_type,
            ref_id=payload.ref_id,
        )
    except svc.TicketRateLimited as e:
        detail = (
            "Слишком много открытых обращений — дождитесь ответа по текущим"
            if e.reason == "too_many_open"
            else "Вы только что создали обращение — подождите немного"
        )
        raise HTTPException(status_code=429, detail=detail)
    await svc.notify_agents_new_ticket(session, ticket=ticket)
    return _ticket_dict(ticket)


@router.get("/tickets")
async def my_tickets(
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    items = await svc.list_for_identity(session, identity.id)
    return {"items": [_ticket_dict(t) for t in items]}


@router.get("/tickets/stream")
async def client_stream(request: Request):
    from api.depends import _identity_from_cookie

    async with async_session_maker() as session:
        identity = await _identity_from_cookie(session, request)
        await session.commit()
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return _sse_response(request, tickets_client_channel(identity.id))


async def _owned(session: AsyncSession, ticket_id: str, identity: Identity) -> Ticket:
    ticket = await session.scalar(
        select(Ticket).where(Ticket.id == ticket_id).with_for_update().execution_options(populate_existing=True)
    )
    if ticket is None or ticket.identity_id != identity.id:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    return ticket


@router.get("/tickets/{ticket_id}")
async def my_ticket_thread(
    ticket_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    ticket = await _owned(session, ticket_id, identity)
    thread = await svc.get_thread(session, ticket_id)
    await svc.mark_read(session, ticket_id=ticket_id, by="client")
    visible = [m for m in thread if m.author != "note"]
    return {
        "ticket": _ticket_dict(ticket),
        "messages": await _message_dicts(session, ticket, visible, identity, "user"),
    }


@router.post("/tickets/{ticket_id}/rate")
async def rate_ticket(
    payload: RatingBody,
    ticket_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    await _owned(session, ticket_id, identity)
    ticket = await svc.set_rating(session, ticket_id=ticket_id, rating=payload.rating)
    if ticket is None:
        raise HTTPException(status_code=400, detail="Оценить можно только закрытое обращение")
    return _ticket_dict(ticket)


@router.post("/tickets/{ticket_id}/messages")
async def my_ticket_reply(
    payload: MessageBody,
    ticket_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    ticket = await _owned(session, ticket_id, identity)
    body = (payload.body or "").strip()
    attachments = _clean_attachments(payload.attachments)
    if not body and not attachments:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    msg = await svc.add_message(
        session,
        ticket_id=ticket_id,
        author="client",
        body=body[:MAX_BODY],
        attachments=attachments,
        author_identity_id=identity.id,
    )
    if svc.forum_enabled():
        ticket = await svc.get_ticket(session, ticket_id)
        if ticket is not None:
            await svc.post_client_message(ticket, body[:MAX_BODY], attachments, session=session, message_id=msg.id)
    return (await _message_dicts(session, ticket, [msg], identity, "user"))[0]


@router.patch("/tickets/{ticket_id}/messages/{message_id}")
async def my_ticket_edit_message(
    payload: EditMessageBody,
    ticket_id: str = Path(...),
    message_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    ticket = await _owned(session, ticket_id, identity)
    return await _edit_ticket_message(session, ticket, message_id, payload, identity, "user")


@router.delete("/tickets/{ticket_id}/messages/{message_id}")
async def my_ticket_delete_message(
    ticket_id: str = Path(...),
    message_id: str = Path(...),
    revision: int | None = Query(None, ge=0),
    session: AsyncSession = Depends(get_session),
    identity: Identity = Depends(verify_identity_token),
) -> dict:
    ticket = await _owned(session, ticket_id, identity)
    return await _delete_ticket_message(session, ticket, message_id, revision, identity, "user")


@admin_router.get("/tickets")
async def agent_inbox(
    status: str | None = Query(None),
    search: str | None = Query(None),
    assigned_to: int | None = Query(None),
    tag: str | None = Query(None),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    total, items = await svc.list_for_agent(
        session, status=status, search=search, assigned_to=assigned_to, tag=tag, limit=limit, offset=offset
    )
    return {"total": total, "items": [_ticket_dict(t) for t in items]}


@admin_router.get("/tickets/unanswered-count")
async def agent_unanswered_count(
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    total, _ = await svc.list_for_agent(session, status=svc.OPEN, limit=1, offset=0)
    return {"count": total}


@admin_router.get("/tickets-metrics")
async def agent_metrics(
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await svc.support_metrics(session)


@admin_router.get("/agents")
async def list_agents(
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    from sqlalchemy import select as _select

    rows = [
        (tg, role)
        for tg, role in (await session.execute(_select(Admin.tg_id, Admin.role))).all()
        if str(role or "admin").strip().lower() != "designer"
    ]
    tg_ids = [int(t) for t, _ in rows if t]
    names: dict[int, str] = {}
    if tg_ids:
        for tg, username, first in (
            await session.execute(_select(User.tg_id, User.username, User.first_name).where(User.tg_id.in_(tg_ids)))
        ).all():
            label = (username and f"@{username}") or first or None
            if tg is not None and label:
                names[int(tg)] = label
    items = []
    for tg, role in rows:
        if not tg:
            continue
        items.append({"tg_id": int(tg), "name": names.get(int(tg)) or str(tg), "role": role or "admin"})
    items.sort(key=lambda x: x["name"].lower())
    return {"items": items}


@admin_router.get("/canned-replies")
async def agent_canned_replies(identity=Depends(verify_identity_agent)) -> dict:
    from core.settings.modes_config import MODES_CONFIG

    raw = MODES_CONFIG.get("SUPPORT_CANNED_REPLIES")
    if not isinstance(raw, list) or not raw:
        raw = svc.DEFAULT_CANNED_REPLIES
    items = []
    for r in raw:
        if isinstance(r, dict) and r.get("body"):
            items.append({"title": str(r.get("title") or "")[:80], "body": str(r.get("body"))[:2000]})
    return {"items": items}


def _sse_response(request: Request, channel: str) -> StreamingResponse:
    async def event_generator():
        redis_client = None
        pubsub = None
        try:
            from redis.asyncio import from_url

            redis_client = from_url(REDIS_URL, encoding="utf-8", decode_responses=True, max_connections=4)
            pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(channel)
            yield "retry: 3000\n\n"
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message and message.get("type") == "message":
                    yield "data: changed\n\n"
                    continue
                yield ": keepalive\n\n"
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning("[Site:Tickets] Поток обновлений оборвался: {}", e)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.close()
                except Exception:
                    pass
            if redis_client is not None:
                try:
                    await redis_client.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@admin_router.get("/tickets/stream")
async def agent_stream(request: Request):
    from api.depends import _identity_from_cookie, resolve_identity_role

    async with async_session_maker() as session:
        identity = await _identity_from_cookie(session, request)
        role = await resolve_identity_role(session, identity) if identity is not None else "user"
        await session.commit()
    if identity is None or role not in ("admin", "moderator"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return _sse_response(request, TICKETS_EVENTS_CHANNEL)


@admin_router.get("/tickets/{ticket_id}")
async def agent_thread(
    ticket_id: str = Path(...),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ticket = await svc.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    thread = await svc.get_thread(session, ticket_id)
    await svc.mark_read(session, ticket_id=ticket_id, by="agent")
    client = await session.get(Identity, ticket.identity_id)
    client_card = None
    context = None
    if client is not None:
        admin_ref = await svc.resolve_billing_user_ref(session, client)
        client_card = {
            "id": client.id,
            "tg_id": client.tg_id,
            "email": client.email,
            "admin_ref": admin_ref,
        }
        context = await svc.build_client_context(session, client)
    return {
        "ticket": _ticket_dict(ticket),
        "messages": await _message_dicts(
            session, ticket, thread, identity, await resolve_identity_role(session, identity)
        ),
        "client": client_card,
        "context": context,
    }


@admin_router.post("/tickets/{ticket_id}/messages")
async def agent_reply(
    payload: MessageBody,
    ticket_id: str = Path(...),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    body = (payload.body or "").strip()
    attachments = _clean_attachments(payload.attachments)
    if not body and not attachments:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    is_note = bool(payload.internal)
    msg = await svc.add_message(
        session,
        ticket_id=ticket_id,
        author="note" if is_note else "agent",
        body=body[:MAX_BODY],
        attachments=None if is_note else attachments,
        agent_tg_id=getattr(identity, "tg_id", None),
        author_identity_id=identity.id,
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    ticket = await svc.get_ticket(session, ticket_id)
    if not is_note and ticket is not None:
        await svc.notify_client_of_reply(session, ticket=ticket, msg=msg)
    if svc.forum_enabled() and ticket is not None:
        prefix = "🔒 Заметка (веб)" if is_note else "🛟 Оператор (веб)"
        await svc.post_system(ticket, f"{prefix}:\n{body[:MAX_BODY]}", session=session, message=msg)
    return (await _message_dicts(session, ticket, [msg], identity, await resolve_identity_role(session, identity)))[0]


@admin_router.patch("/tickets/{ticket_id}/messages/{message_id}")
async def agent_edit_message(
    payload: EditMessageBody,
    ticket_id: str = Path(...),
    message_id: str = Path(...),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ticket = await svc.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    role = await resolve_identity_role(session, identity)
    return await _edit_ticket_message(session, ticket, message_id, payload, identity, role)


@admin_router.delete("/tickets/{ticket_id}/messages/{message_id}")
async def agent_delete_message(
    ticket_id: str = Path(...),
    message_id: str = Path(...),
    revision: int | None = Query(None, ge=0),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ticket = await svc.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    role = await resolve_identity_role(session, identity)
    return await _delete_ticket_message(session, ticket, message_id, revision, identity, role)


@admin_router.post("/tickets/{ticket_id}/messages/{message_id}/retry")
async def agent_retry_message_delivery(
    ticket_id: str = Path(...),
    message_id: str = Path(...),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Повторяет неудачную доставку текущей версии сообщения."""
    ticket = await session.scalar(
        select(Ticket).where(Ticket.id == ticket_id).with_for_update().execution_options(populate_existing=True)
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    msg = await session.scalar(
        select(TicketMessage)
        .where(TicketMessage.id == message_id, TicketMessage.ticket_id == ticket.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    rows = (
        await session.scalars(
            select(TicketDelivery)
            .where(
                TicketDelivery.ticket_id == ticket.id,
                TicketDelivery.ticket_message_id == msg.id,
                TicketDelivery.kind.in_(_MESSAGE_DELIVERY_KINDS),
                TicketDelivery.status == "failed",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    now = datetime.now(UTC)
    changed = False
    for row in rows:
        if (row.payload or {}).get("revision") != msg.revision:
            continue
        row.status = "pending"
        row.next_retry_at = now
        row.lease_token = None
        row.lease_until = None
        row.last_error = None
        row.updated_at = now
        changed = True
    if changed:
        await session.flush()
        await publish_tickets_changed(session, ticket.identity_id, client=msg.author != "note")
    return {"ok": True}


@admin_router.patch("/tickets/{ticket_id}")
async def agent_patch(
    payload: AgentPatchBody,
    ticket_id: str = Path(...),
    identity=Depends(verify_identity_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ticket = await svc.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    if payload.status is not None:
        was_closed = ticket.status == svc.CLOSED
        if await svc.set_status(session, ticket_id=ticket_id, status=payload.status) is None:
            raise HTTPException(status_code=400, detail="Недопустимый статус")
        if svc.forum_enabled() and payload.status in (svc.CLOSED, svc.OPEN):
            await svc.set_topic_state(ticket, closed=payload.status == svc.CLOSED, session=session)
        if payload.status == svc.CLOSED and not was_closed:
            await svc.notify_client_ticket_closed(session, ticket=ticket)
    if payload.unassign:
        await svc.assign_ticket(session, ticket_id=ticket_id, agent_tg_id=None)
    elif payload.assigned_agent_tg_id is not None:
        await svc.assign_ticket(session, ticket_id=ticket_id, agent_tg_id=payload.assigned_agent_tg_id)
    if payload.priority is not None:
        if await svc.set_priority(session, ticket_id=ticket_id, priority=payload.priority) is None:
            raise HTTPException(status_code=400, detail="Недопустимый приоритет")
    if payload.tags is not None:
        await svc.set_tags(session, ticket_id=ticket_id, tags=payload.tags)
    fresh = await svc.get_ticket(session, ticket_id)
    return _ticket_dict(fresh)


@admin_router.delete("/tickets/{ticket_id}")
async def agent_delete(
    ticket_id: str = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ok = await svc.delete_ticket(session, ticket_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Тикет не найден")
    return {"ok": True}
