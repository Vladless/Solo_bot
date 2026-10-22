from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import UserId
from database.models import Key, Notification, SubscriptionEvent


RETURNING_NOTIFICATION_TYPE = "returning"


async def get_returning_targets(session: AsyncSession, min_days: int, max_days: int) -> list[int]:
    """Возвращает ушедших клиентов для напоминания о возвращении."""
    now = datetime.utcnow()
    lo = now - timedelta(days=max_days)
    hi = now - timedelta(days=min_days)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    active = select(Key.user_id).where(Key.expiry_time > now_ms).distinct()
    came_back = (
        select(SubscriptionEvent.user_id)
        .where(SubscriptionEvent.event_type.in_(["created", "renewed"]))
        .where(SubscriptionEvent.created_at >= lo)
        .distinct()
    )
    already = select(Notification.user_id).where(Notification.notification_type == RETURNING_NOTIFICATION_TYPE)

    stmt = (
        select(SubscriptionEvent.user_id)
        .where(SubscriptionEvent.event_type.in_(["expired", "deleted"]))
        .where(SubscriptionEvent.created_at >= lo)
        .where(SubscriptionEvent.created_at <= hi)
        .where(SubscriptionEvent.user_id.isnot(None))
        .where(~SubscriptionEvent.user_id.in_(active))
        .where(~SubscriptionEvent.user_id.in_(came_back))
        .where(~SubscriptionEvent.user_id.in_(already))
        .distinct()
    )
    result = await session.execute(stmt)
    return [UserId(uid) for uid in result.scalars().all()]
