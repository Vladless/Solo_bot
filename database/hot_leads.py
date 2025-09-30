from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Payment, User


async def get_hot_leads(session: AsyncSession):
    now_ms = func.extract("epoch", func.now()) * 1000

    sub_active = select(Key.tg_id).where(Key.expiry_time > now_ms).distinct()

    stmt = (
        select(Payment.tg_id)
        .join(User, User.tg_id == Payment.tg_id)
        .distinct()
        .where(User.trial == 1)
        .where(Payment.amount > 0)
        .where(Payment.status == "success")
        .where(Payment.payment_system.notin_(["referral", "coupon", "cashback"]))
        .where(~Payment.tg_id.in_(sub_active))
    )

    result = await session.execute(stmt)
    return result.scalars().all()
