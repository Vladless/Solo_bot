from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    BlockedUser,
    CouponUsage,
    Gift,
    GiftUsage,
    Key,
    ManualBan,
    Notification,
    Payment,
    Referral,
    SubscriptionEvent,
    TemporaryData,
    User,
)
from database.partners import freeze_partner_owners, refresh_partner_tg_mirrors


TG_FOREIGN_KEY_MIRRORS: tuple[tuple[type, str], ...] = (
    (Key, "tg_id"),
    (Payment, "tg_id"),
    (Gift, "sender_tg_id"),
    (Gift, "recipient_tg_id"),
)


async def freeze_legacy_tg_owner(session: AsyncSession, user_id: int) -> None:
    """Закрепляет владельца старых записей перед сменой Telegram."""
    tg_id = await session.scalar(select(User.tg_id).where(User.id == user_id).with_for_update())
    if tg_id is None:
        return
    await freeze_partner_owners(session, user_id)
    for model, owner_column, tg_column in (
        (Key, "user_id", "tg_id"),
        (Payment, "user_id", "tg_id"),
        (Gift, "sender_user_id", "sender_tg_id"),
        (Gift, "recipient_user_id", "recipient_tg_id"),
        (SubscriptionEvent, "user_id", "tg_id"),
    ):
        await session.execute(
            update(model)
            .where(getattr(model, owner_column).is_(None), getattr(model, tg_column) == tg_id)
            .values({owner_column: user_id})
        )


async def release_tg_mirrors(session: AsyncSession, tg_id: int) -> None:
    """Освобождает ссылки на Telegram перед его отвязкой."""
    owner_id = await session.scalar(select(User.id).where(User.tg_id == tg_id).with_for_update())
    if owner_id is not None:
        await freeze_legacy_tg_owner(session, owner_id)
    for model, column in TG_FOREIGN_KEY_MIRRORS:
        await session.execute(update(model).where(getattr(model, column) == tg_id).values({column: None}))


async def refresh_tg_mirrors_for_user(session: AsyncSession, user_id: int) -> None:
    r = await session.execute(select(User.tg_id).where(User.id == user_id))
    tg = r.scalar_one_or_none()

    await session.execute(update(Key).where(Key.user_id == user_id).values(tg_id=tg))
    await session.execute(update(Payment).where(Payment.user_id == user_id).values(tg_id=tg))
    await session.execute(update(Notification).where(Notification.user_id == user_id).values(tg_id=tg))
    await session.execute(update(GiftUsage).where(GiftUsage.user_id == user_id).values(tg_id=tg))
    await session.execute(update(CouponUsage).where(CouponUsage.user_id == user_id).values(tg_id=tg))
    await session.execute(update(TemporaryData).where(TemporaryData.user_id == user_id).values(tg_id=tg))
    await session.execute(update(BlockedUser).where(BlockedUser.user_id == user_id).values(tg_id=tg))
    await session.execute(update(ManualBan).where(ManualBan.user_id == user_id).values(tg_id=tg))

    await session.execute(update(Referral).where(Referral.referred_user_id == user_id).values(referred_tg_id=tg))
    await session.execute(update(Referral).where(Referral.referrer_user_id == user_id).values(referrer_tg_id=tg))

    await session.execute(update(Gift).where(Gift.sender_user_id == user_id).values(sender_tg_id=tg))
    await session.execute(update(Gift).where(Gift.recipient_user_id == user_id).values(recipient_tg_id=tg))
    await refresh_partner_tg_mirrors(session, user_id)
