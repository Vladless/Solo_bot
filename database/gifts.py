from datetime import datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import resolve_user_optional
from database.models import Gift, GiftUsage
from logger import logger


async def store_gift_link(
    session: AsyncSession,
    gift_id: str,
    sender_legacy_ref: int,
    selected_months: int,
    expiry_time: datetime,
    gift_link: str,
    tariff_id: int | None = None,
    is_unlimited: bool = False,
    max_usages: int | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
    selected_price_rub: int | None = None,
) -> bool:
    u = await resolve_user_optional(session, sender_legacy_ref)
    if u is None:
        raise ValueError(f"sender not found for gift: {sender_legacy_ref}")
    stmt = insert(Gift).values(
        gift_id=gift_id,
        sender_user_id=u.id,
        sender_tg_id=u.tg_id,
        recipient_user_id=None,
        selected_months=selected_months,
        expiry_time=expiry_time,
        gift_link=gift_link,
        created_at=datetime.utcnow(),
        is_used=False,
        tariff_id=tariff_id,
        is_unlimited=is_unlimited,
        max_usages=max_usages,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
        selected_price_rub=selected_price_rub,
    )
    await session.execute(stmt)
    logger.info(
        f"🎁 Подарок {gift_id} сохранён "
        f"(tariff_id={tariff_id}, max_usages={max_usages}, "
        f"device={selected_device_limit}, traffic={selected_traffic_gb}, price={selected_price_rub})"
    )
    return True


async def get_gift_locked(session: AsyncSession, gift_id: str) -> Gift | None:
    """Блокирует подарок для безопасной активации."""
    result = await session.execute(select(Gift).where(Gift.gift_id == gift_id).with_for_update())
    return result.scalar_one_or_none()


async def delete_gift_for_user(session: AsyncSession, gift_id: str, user_id: int) -> bool:
    """Удаляет подарок только его владельца по каноническому users.id."""
    gift = (
        await session.execute(
            select(Gift).where(Gift.gift_id == gift_id, Gift.sender_user_id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    if gift is None:
        return False
    await session.execute(delete(GiftUsage).where(GiftUsage.gift_id == gift_id))
    result = await session.execute(delete(Gift).where(Gift.gift_id == gift_id, Gift.sender_user_id == user_id))
    return bool(result.rowcount)


async def get_gift_usage(session: AsyncSession, gift_id: str, user_id: int) -> GiftUsage | None:
    """Возвращает запись об использовании подарка конкретным пользователем, если есть."""
    result = await session.execute(
        select(GiftUsage).where(
            GiftUsage.gift_id == gift_id,
            GiftUsage.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def count_gift_usages(session: AsyncSession, gift_id: str) -> int:
    """Считает активации подарка."""
    result = await session.execute(select(func.count()).select_from(GiftUsage).where(GiftUsage.gift_id == gift_id))
    return int(result.scalar_one() or 0)


async def record_gift_usage(
    session: AsyncSession,
    gift_id: str,
    user_id: int,
    tg_id: int | None,
) -> None:
    """Сохраняет использование подарка клиентом."""
    await session.execute(
        insert(GiftUsage).values(
            gift_id=gift_id,
            user_id=user_id,
            tg_id=tg_id,
        )
    )


async def mark_gift_fully_redeemed(
    session: AsyncSession,
    gift_id: str,
    recipient_user_id: int,
    recipient_tg_id: int | None,
) -> None:
    """Отмечает подарок использованным и сохраняет получателя."""
    await session.execute(
        update(Gift)
        .where(Gift.gift_id == gift_id)
        .values(
            is_used=True,
            recipient_user_id=recipient_user_id,
            recipient_tg_id=recipient_tg_id,
        )
    )
