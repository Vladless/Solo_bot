from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import PAYMENT_SYSTEMS_EXCLUDED
from database.models import Key, Payment, Tariff, User


async def get_cold_leads(session: AsyncSession):
    now_ms = func.extract("epoch", func.now()) * 1000

    sub_active = select(Key.user_id).where(Key.expiry_time > now_ms).distinct()
    paid = (
        select(Payment.user_id)
        .where(Payment.amount > 0)
        .where(Payment.status == "success")
        .where(Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED))
        .distinct()
    )

    users_with_keys = select(Key.user_id).where(Key.user_id.isnot(None)).distinct()

    trial_tariffs = select(Tariff.id).where(Tariff.group_code == "trial")
    expired_trial_users = (
        select(Key.user_id)
        .where(Key.user_id.isnot(None))
        .where(Key.tariff_id.in_(trial_tariffs))
        .where(Key.expiry_time < now_ms)
        .distinct()
    )

    stmt = (
        select(User.id)
        .where(~User.id.in_(sub_active))
        .where(~User.id.in_(paid))
        .where(
            or_(
                ~User.id.in_(users_with_keys),
                User.id.in_(expired_trial_users),
            )
        )
    )

    result = await session.execute(stmt)
    return result.scalars().all()
