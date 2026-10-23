import asyncio
import copy
import uuid

from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from core.redis_cache import cache_publish
from database import db
from database.models import Ticket, TicketDelivery, TicketMessage
from logger import logger


_LEASE_SECONDS = 90
_KINDS = {
    "event",
    "telegram_forum",
    "telegram_client",
    "telegram_agent",
    "telegram_sync",
    "telegram_notice",
    "forum_state",
    "forum_delete",
}
_ORDERED_KINDS = {"telegram_forum", "telegram_client", "telegram_agent"}


async def enqueue(
    session,
    *,
    kind: str,
    ticket_id: str | None = None,
    ticket_message_id: str | None = None,
    payload: dict | None = None,
    dedupe_key: str | None = None,
) -> TicketDelivery:
    """Сохраняет доставку вместе с изменением обращения."""
    if not kind or len(kind) > 32:
        raise ValueError("Некорректный тип доставки")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("Некорректные параметры доставки")
    if dedupe_key is not None and (not dedupe_key or len(dedupe_key) > 255):
        raise ValueError("Некорректный ключ доставки")
    if dedupe_key is not None:
        existing = await session.scalar(select(TicketDelivery).where(TicketDelivery.dedupe_key == dedupe_key))
        if existing is not None:
            return existing
    row = TicketDelivery(
        kind=kind,
        ticket_id=ticket_id,
        ticket_message_id=ticket_message_id,
        payload=copy.deepcopy(payload or {}),
        dedupe_key=dedupe_key,
    )
    if dedupe_key is None:
        session.add(row)
        await session.flush()
        return row
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(TicketDelivery).where(TicketDelivery.dedupe_key == dedupe_key))
        if existing is None:
            raise
        return existing
    return row


def _eligible(now):
    """Выбирает готовую доставку с соблюдением порядка реплик."""
    prior = aliased(TicketDelivery)
    waiting_for_prior = exists(
        select(prior.id)
        .where(
            prior.ticket_id == TicketDelivery.ticket_id,
            prior.kind == TicketDelivery.kind,
            prior.status.in_(["pending", "retry", "running"]),
            or_(
                prior.created_at < TicketDelivery.created_at,
                and_(prior.created_at == TicketDelivery.created_at, prior.id < TicketDelivery.id),
            ),
        )
        .correlate(TicketDelivery)
    )
    return and_(
        TicketDelivery.kind.in_(_KINDS),
        TicketDelivery.status.in_(["pending", "retry", "running"]),
        or_(TicketDelivery.next_retry_at.is_(None), TicketDelivery.next_retry_at <= now),
        or_(TicketDelivery.lease_until.is_(None), TicketDelivery.lease_until <= now),
        or_(TicketDelivery.kind.not_in(_ORDERED_KINDS), ~waiting_for_prior),
    )


async def _claim_one(*, sessionmaker=None) -> dict | None:
    """Атомарно арендует одну доставку до сетевого запроса."""
    now = datetime.now(UTC)
    maker = sessionmaker or db.async_session_maker
    async with maker() as session:
        row = await session.scalar(
            select(TicketDelivery)
            .where(_eligible(now))
            .order_by(TicketDelivery.created_at, TicketDelivery.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        token = uuid.uuid4().hex
        claimed = await session.execute(
            update(TicketDelivery)
            .where(TicketDelivery.id == row.id, _eligible(now))
            .values(
                status="running",
                attempts=TicketDelivery.attempts + 1,
                lease_token=token,
                lease_until=now + timedelta(seconds=_LEASE_SECONDS),
                next_retry_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            await session.rollback()
            return None
        snapshot = {
            "id": row.id,
            "kind": row.kind,
            "ticket_id": row.ticket_id,
            "ticket_message_id": row.ticket_message_id,
            "payload": copy.deepcopy(row.payload),
            "lease_token": token,
            "attempts": row.attempts + 1,
        }
        await session.commit()
        return snapshot


async def _dispatch(job: dict, *, sessionmaker=None) -> None:
    """Доставляет сохранённое событие или сообщение поддержки."""
    if job["kind"] != "event":
        from .worker import deliver

        await deliver(job, sessionmaker=sessionmaker or db.async_session_maker)
        return
    channel = job["payload"].get("channel")
    if not isinstance(channel, str) or not (
        channel == "tickets:events" or (channel.startswith("tickets:client:") and len(channel) > len("tickets:client:"))
    ):
        raise ValueError("Некорректный канал события поддержки")
    await cache_publish(channel, {"t": "changed"}, strict=True)


async def _finish(job: dict, error: Exception | None = None, *, sessionmaker=None) -> bool:
    """Фиксирует результат только для действующего владельца аренды."""
    now = datetime.now(UTC)
    failed = isinstance(error, ValueError | TelegramBadRequest | TelegramForbiddenError)
    delay = min(3600, 5 * (2 ** min(max(job["attempts"] - 1, 0), 10)))
    if isinstance(error, TelegramRetryAfter):
        delay = max(1, int(error.retry_after))
    maker = sessionmaker or db.async_session_maker
    async with maker() as session:
        result = await session.execute(
            update(TicketDelivery)
            .where(
                TicketDelivery.id == job["id"],
                TicketDelivery.status == "running",
                TicketDelivery.lease_token == job["lease_token"],
            )
            .values(
                status="done" if error is None else "failed" if failed else "retry",
                next_retry_at=now + timedelta(seconds=delay) if error is not None and not failed else None,
                lease_token=None,
                lease_until=None,
                last_error=type(error).__name__ if error is not None else None,
                updated_at=now,
            )
        )
        if result.rowcount == 1 and job["kind"] != "event" and job.get("ticket_id"):
            ticket = await session.get(Ticket, job["ticket_id"])
            if ticket is not None:
                from .events import publish_tickets_changed

                msg = (
                    await session.get(TicketMessage, job["ticket_message_id"]) if job.get("ticket_message_id") else None
                )
                await publish_tickets_changed(session, ticket.identity_id, client=msg is None or msg.author != "note")
        await session.commit()
        if error is not None and result.rowcount == 1:
            logger.warning("[TicketsDelivery] job={} kind={} error={}", job["id"], job["kind"], type(error).__name__)
        return result.rowcount == 1


async def _cleanup_events(*, sessionmaker=None) -> None:
    """Удаляет ограниченную порцию завершённых старых событий."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    old_ids = (
        select(TicketDelivery.id)
        .where(TicketDelivery.kind == "event", TicketDelivery.status == "done", TicketDelivery.updated_at < cutoff)
        .order_by(TicketDelivery.updated_at, TicketDelivery.id)
        .limit(100)
    )
    maker = sessionmaker or db.async_session_maker
    async with maker() as session:
        old = list((await session.scalars(old_ids)).all())
        if not old:
            return
        await session.execute(
            delete(TicketDelivery)
            .where(
                TicketDelivery.id.in_(old),
                TicketDelivery.kind == "event",
                TicketDelivery.status == "done",
                TicketDelivery.updated_at < cutoff,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()


async def process_due(limit: int = 20, *, sessionmaker=None) -> int:
    """Обрабатывает ограниченную порцию сохранённых доставок."""
    await _cleanup_events(sessionmaker=sessionmaker)
    completed = 0
    for _ in range(max(0, min(int(limit), 100))):
        job = await _claim_one(sessionmaker=sessionmaker)
        if job is None:
            break
        try:
            async with asyncio.timeout(30):
                await _dispatch(job, sessionmaker=sessionmaker)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await _finish(job, error, sessionmaker=sessionmaker)
        else:
            if await _finish(job, sessionmaker=sessionmaker):
                completed += 1
    return completed
