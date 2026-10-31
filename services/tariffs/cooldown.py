from datetime import UTC, datetime, timedelta
from math import ceil

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.tariffs_config import TARIFFS_CONFIG
from database.access.resolution import UserId, resolve_user_optional
from database.models import SubscriptionEvent
from services.errors import ValidationError


def ensure_tariff_switch_available(key, *, now_ms: int | None = None) -> None:
    """Проверяет интервал между сменами тарифа подписки."""
    days = int(TARIFFS_CONFIG.get("TARIFF_SWITCH_COOLDOWN_DAYS", 0) or 0)
    if days <= 0:
        return
    last_change = (
        key.get("last_tariff_change_at") if isinstance(key, dict) else getattr(key, "last_tariff_change_at", None)
    )
    if last_change is None:
        return
    if last_change.tzinfo is None:
        last_change = last_change.replace(tzinfo=UTC)
    now = datetime.now(UTC) if now_ms is None else datetime.fromtimestamp(now_ms / 1000, UTC)
    remaining = ceil((last_change - now).total_seconds() + days * 86400)
    if remaining > 0:
        left = format_cooldown_left(remaining) if remaining >= 60 else "меньше минуты"
        raise ValidationError(
            f"Менять тариф этой подписки можно раз в {days} дн. Следующая смена доступна через {left}."
        )


async def get_tariff_cooldown_remaining(session: AsyncSession, tg_id: int, tariff_id: int, cooldown_days: int) -> int:
    cooldown_days = int(cooldown_days or 0)
    if cooldown_days <= 0 or not tariff_id:
        return 0

    user = await resolve_user_optional(session, tg_id)
    if user is None and isinstance(tg_id, UserId):
        return 0
    refs = []
    if user is not None:
        refs.append(SubscriptionEvent.user_id == user.id)
    telegram_id = user.tg_id if user is not None else tg_id
    if telegram_id is not None:
        refs.append(and_(SubscriptionEvent.user_id.is_(None), SubscriptionEvent.tg_id == telegram_id))

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
