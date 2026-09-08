from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    DateTime as SQLADateTime,
    and_,
    cast,
    delete,
    desc,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import PAYMENT_SYSTEMS_EXCLUDED
from database.models import AuditEvent, Payment


_AUDIT_TABLE_READY = False


async def ensure_audit_table(session: AsyncSession) -> None:
    global _AUDIT_TABLE_READY
    if _AUDIT_TABLE_READY:
        return
    connection = await session.connection()
    await connection.run_sync(AuditEvent.__table__.create, checkfirst=True)
    _AUDIT_TABLE_READY = True


async def delete_old_audit_events_db(
    session: AsyncSession,
    *,
    older_than_days: int = 90,
) -> int:
    await ensure_audit_table(session)
    threshold = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    stmt = delete(AuditEvent).where(AuditEvent.created_at < threshold)
    result = await session.execute(stmt)
    return result.rowcount or 0


async def fetch_audit_rows_db(
    session: AsyncSession,
    *,
    date_from: datetime,
    date_to: datetime,
    limit: int,
) -> list[tuple[str, str, int | None, str | None]]:
    await ensure_audit_table(session)
    stmt = (
        select(
            AuditEvent.path_or_handler,
            AuditEvent.result,
            AuditEvent.actor_tg_id,
            AuditEvent.actor_identity_id,
        )
        .where(
            AuditEvent.event_type != "audit_reset",
            AuditEvent.created_at >= date_from,
            AuditEvent.created_at < date_to,
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.all())


async def fetch_successful_payment_rows_db(
    session: AsyncSession,
    *,
    date_from: datetime,
    date_to: datetime,
    limit: int,
) -> list[tuple[str, str, int | None, None]]:
    success_at_expr = func.coalesce(
        cast(Payment.metadata_["status_changed_at"].astext, SQLADateTime),
        Payment.created_at,
    )
    stmt = (
        select(Payment.payment_system, Payment.payment_id, Payment.user_id)
        .where(
            Payment.status == "success",
            Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED),
            success_at_expr >= date_from,
            success_at_expr < date_to,
        )
        .order_by(desc(success_at_expr))
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        (f"payment_success:{payment_system or '-'}:{payment_id or '-'}", "success", tg_id, None)
        for payment_system, payment_id, tg_id in result.all()
    ]


async def fetch_latest_audit_reset_db(
    session: AsyncSession,
    *,
    source: str = "db",
) -> datetime | None:
    await ensure_audit_table(session)
    stmt = select(func.max(AuditEvent.created_at)).where(
        AuditEvent.event_type == "audit_reset",
        AuditEvent.channel == "system",
        AuditEvent.path_or_handler == f"audit_reset:{source}",
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_audit_reset_marker_db(
    session: AsyncSession,
    *,
    source: str = "db",
    created_at: datetime | None = None,
) -> datetime:
    await ensure_audit_table(session)
    created = created_at or datetime.utcnow()
    event = AuditEvent(
        event_type="audit_reset",
        channel="system",
        path_or_handler=f"audit_reset:{source}",
        result="success",
        created_at=created,
    )
    session.add(event)
    await session.flush()
    return created


async def fetch_existing_audit_request_ids_db(
    session: AsyncSession,
    request_ids: Iterable[str],
) -> set[str]:
    await ensure_audit_table(session)
    request_ids_list = sorted({rid for rid in request_ids if rid})
    if not request_ids_list:
        return set()
    stmt = select(AuditEvent.request_id).where(AuditEvent.request_id.in_(request_ids_list))
    result = await session.execute(stmt)
    return {rid for rid in result.scalars().all() if rid}


async def fetch_audit_events_db(
    session: AsyncSession,
    *,
    identity_id: str | None = None,
    tg_id: int | None = None,
    channel: str | None = None,
    event_type: str | None = None,
    event_types: Iterable[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEvent]:
    await ensure_audit_table(session)
    stmt = select(AuditEvent)
    actor_filters = []
    if identity_id:
        actor_filters.append(AuditEvent.actor_identity_id == identity_id)
        actor_filters.append(and_(AuditEvent.entity_type == "identity", AuditEvent.entity_id == identity_id))
    if tg_id is not None:
        tg_id_str = str(tg_id)
        actor_filters.append(AuditEvent.actor_tg_id == tg_id)
        actor_filters.append(and_(AuditEvent.entity_type == "user", AuditEvent.entity_id == tg_id_str))
        actor_filters.append(and_(AuditEvent.entity_type == "telegram_user", AuditEvent.entity_id == tg_id_str))
    if actor_filters:
        stmt = stmt.where(or_(*actor_filters))
    if channel:
        stmt = stmt.where(AuditEvent.channel == channel)
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    event_types_list = sorted(event_types) if event_types else None
    if event_types_list:
        stmt = stmt.where(AuditEvent.event_type.in_(event_types_list))
    stmt = stmt.order_by(desc(AuditEvent.created_at), desc(AuditEvent.id)).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def fetch_audit_events_db_window(
    session: AsyncSession,
    *,
    identity_id: str | None = None,
    tg_id: int | None = None,
    channel: str | None = None,
    event_type: str | None = None,
    event_types: Iterable[str] | None = None,
    limit: int = 5000,
) -> list[AuditEvent]:
    return await fetch_audit_events_db(
        session,
        identity_id=identity_id,
        tg_id=tg_id,
        channel=channel,
        event_type=event_type,
        event_types=event_types,
        limit=limit,
        offset=0,
    )
