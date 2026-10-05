from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import resolve_user_optional
from database.models import ScheduledBroadcast


SCHEDULED_BROADCAST_STATUS_DRAFT = "draft"
SCHEDULED_BROADCAST_STATUS_SCHEDULED = "scheduled"
SCHEDULED_BROADCAST_STATUS_RUNNING = "running"
SCHEDULED_BROADCAST_STATUS_SENT = "sent"
SCHEDULED_BROADCAST_STATUS_CANCELLED = "cancelled"
SCHEDULED_BROADCAST_STATUS_FAILED = "failed"

EDITABLE_SCHEDULED_BROADCAST_STATUSES = {
    SCHEDULED_BROADCAST_STATUS_DRAFT,
    SCHEDULED_BROADCAST_STATUS_SCHEDULED,
    SCHEDULED_BROADCAST_STATUS_FAILED,
}


async def create_scheduled_broadcast(
    session: AsyncSession,
    *,
    created_by_tg_id: int | None,
    send_to: str,
    cluster_name: str | None,
    text: str,
    photo: str | None,
    keyboard_json: dict | None,
    scheduled_for: datetime,
    workers: int,
    messages_per_second: int,
    status: str = SCHEDULED_BROADCAST_STATUS_SCHEDULED,
    channel: str = "both",
) -> ScheduledBroadcast:
    created_by_uid = None
    mirror_tg = created_by_tg_id
    if created_by_tg_id is not None:
        cu = await resolve_user_optional(session, created_by_tg_id)
        if cu is not None:
            created_by_uid = cu.id
            mirror_tg = cu.tg_id
    broadcast = ScheduledBroadcast(
        created_by_user_id=created_by_uid,
        created_by_tg_id=mirror_tg,
        send_to=send_to,
        channel=channel,
        cluster_name=cluster_name,
        text=text,
        photo=photo,
        keyboard_json=keyboard_json,
        scheduled_for=scheduled_for,
        workers=workers,
        messages_per_second=messages_per_second,
        status=status,
    )
    session.add(broadcast)
    await session.flush()
    await session.refresh(broadcast)
    return broadcast


async def get_scheduled_broadcast(session: AsyncSession, broadcast_id: str) -> ScheduledBroadcast | None:
    result = await session.execute(select(ScheduledBroadcast).where(ScheduledBroadcast.id == broadcast_id))
    return result.scalar_one_or_none()


async def list_scheduled_broadcasts(
    session: AsyncSession,
    *,
    statuses: list[str] | None = None,
    created_by_tg_id: int | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[ScheduledBroadcast]:
    stmt = select(ScheduledBroadcast)
    if statuses:
        stmt = stmt.where(ScheduledBroadcast.status.in_(statuses))
    if created_by_tg_id is not None:
        stmt = stmt.where(ScheduledBroadcast.created_by_tg_id == created_by_tg_id)
    stmt = stmt.order_by(ScheduledBroadcast.scheduled_for.asc(), ScheduledBroadcast.created_at.desc())
    stmt = stmt.offset(max(0, offset)).limit(max(1, min(limit, 100)))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_scheduled_broadcast(
    session: AsyncSession,
    broadcast_id: str,
    **values,
) -> ScheduledBroadcast | None:
    values["updated_at"] = datetime.utcnow()
    result = await session.execute(
        update(ScheduledBroadcast)
        .where(
            ScheduledBroadcast.id == broadcast_id,
            ScheduledBroadcast.status.in_(EDITABLE_SCHEDULED_BROADCAST_STATUSES),
        )
        .values(**values)
    )
    if not result.rowcount:
        return None
    return await get_scheduled_broadcast(session, broadcast_id)


async def cancel_scheduled_broadcast(session: AsyncSession, broadcast_id: str) -> ScheduledBroadcast | None:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(ScheduledBroadcast)
        .where(
            ScheduledBroadcast.id == broadcast_id,
            ScheduledBroadcast.status.in_(EDITABLE_SCHEDULED_BROADCAST_STATUSES),
        )
        .values(
            status=SCHEDULED_BROADCAST_STATUS_CANCELLED,
            cancelled_at=now,
            updated_at=datetime.utcnow(),
        )
    )
    if not result.rowcount:
        return None
    return await get_scheduled_broadcast(session, broadcast_id)


async def claim_due_scheduled_broadcasts(session: AsyncSession, limit: int = 10) -> list[ScheduledBroadcast]:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(ScheduledBroadcast.id)
        .where(
            ScheduledBroadcast.status == SCHEDULED_BROADCAST_STATUS_SCHEDULED,
            ScheduledBroadcast.scheduled_for <= now,
        )
        .order_by(ScheduledBroadcast.scheduled_for.asc(), ScheduledBroadcast.created_at.asc())
        .limit(max(1, min(limit, 50)))
    )
    claimed_ids: list[str] = []
    for broadcast_id in [row[0] for row in result.all()]:
        claim_result = await session.execute(
            update(ScheduledBroadcast)
            .where(
                ScheduledBroadcast.id == broadcast_id,
                ScheduledBroadcast.status == SCHEDULED_BROADCAST_STATUS_SCHEDULED,
            )
            .values(
                status=SCHEDULED_BROADCAST_STATUS_RUNNING,
                started_at=now,
                cancelled_at=None,
                error_text=None,
                updated_at=datetime.utcnow(),
            )
        )
        if claim_result.rowcount:
            claimed_ids.append(broadcast_id)
    if not claimed_ids:
        return []
    result = await session.execute(
        select(ScheduledBroadcast)
        .where(ScheduledBroadcast.id.in_(claimed_ids))
        .order_by(ScheduledBroadcast.scheduled_for.asc(), ScheduledBroadcast.created_at.asc())
    )
    return list(result.scalars().all())


async def start_scheduled_broadcast(session: AsyncSession, broadcast_id: str) -> ScheduledBroadcast | None:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(ScheduledBroadcast)
        .where(
            ScheduledBroadcast.id == broadcast_id,
            ScheduledBroadcast.status.in_(EDITABLE_SCHEDULED_BROADCAST_STATUSES),
        )
        .values(
            status=SCHEDULED_BROADCAST_STATUS_RUNNING,
            scheduled_for=now,
            started_at=now,
            cancelled_at=None,
            error_text=None,
            updated_at=datetime.utcnow(),
        )
    )
    if not result.rowcount:
        return None
    return await get_scheduled_broadcast(session, broadcast_id)


async def mark_scheduled_broadcast_sent(
    session: AsyncSession,
    broadcast_id: str,
    stats: dict,
) -> ScheduledBroadcast | None:
    now = datetime.now(timezone.utc)
    await session.execute(
        update(ScheduledBroadcast)
        .where(ScheduledBroadcast.id == broadcast_id)
        .values(
            status=SCHEDULED_BROADCAST_STATUS_SENT,
            sent_at=now,
            stats_json=stats,
            error_text=None,
            updated_at=datetime.utcnow(),
        )
    )
    return await get_scheduled_broadcast(session, broadcast_id)


async def mark_scheduled_broadcast_failed(
    session: AsyncSession,
    broadcast_id: str,
    error_text: str,
) -> ScheduledBroadcast | None:
    await session.execute(
        update(ScheduledBroadcast)
        .where(ScheduledBroadcast.id == broadcast_id)
        .values(
            status=SCHEDULED_BROADCAST_STATUS_FAILED,
            error_text=error_text,
            updated_at=datetime.utcnow(),
        )
    )
    return await get_scheduled_broadcast(session, broadcast_id)
