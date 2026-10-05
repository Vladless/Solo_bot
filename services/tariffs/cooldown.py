from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import resolve_user_optional
from database.models import SubscriptionEvent


async def get_tariff_cooldown_remaining(session: AsyncSession, tg_id: int, tariff_id: int, cooldown_days: int) -> int:
    cooldown_days = int(cooldown_days or 0)
    if cooldown_days <= 0 or not tariff_id:
        return 0

    user = await resolve_user_optional(session, tg_id)
    refs = [SubscriptionEvent.tg_id == tg_id]
    if user is not None:
        refs.append(SubscriptionEvent.user_id == user.id)

    last = await session.scalar(
        select(SubscriptionEvent.created_at)
        .where(
            SubscriptionEvent.tariff_id == int(tariff_id),
            SubscriptionEvent.event_type.in_(("created", "renewed")),
            or_(*refs),
        )
        .order_by(SubscriptionEvent.created_at.desc())
        .limit(1)
    )
    if last is None:
        return 0

    remaining = timedelta(days=cooldown_days) - (datetime.utcnow() - last)
    return max(0, int(remaining.total_seconds()))


def format_cooldown_left(seconds: int) -> str:
    if seconds <= 0:
        return "0 мин"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days > 0:
        return f"{days} дн" + (f" {hours} ч" if hours else "")
    if hours > 0:
        return f"{hours} ч" + (f" {minutes} мин" if minutes else "")
    return f"{minutes} мин"
