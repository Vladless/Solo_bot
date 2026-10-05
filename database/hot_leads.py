from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.constants import PAYMENT_SYSTEMS_EXCLUDED
from database.models import Key, Payment, User


async def get_hot_leads(session: AsyncSession) -> list[tuple[int, datetime]]:
    """Клиенты с закончившейся оплаченной подпиской: (users.id, момент окончания)."""
    now_ms = func.extract("epoch", func.now()) * 1000

    sub_active = select(Key.user_id).where(Key.expiry_time > now_ms).distinct()
    last_expiry = (
        select(Key.user_id, func.max(Key.expiry_time).label("expiry_time")).group_by(Key.user_id).subquery()
    )

    stmt = (
        select(Payment.user_id, last_expiry.c.expiry_time)
        .join(User, User.id == Payment.user_id)
        .join(last_expiry, last_expiry.c.user_id == Payment.user_id)
        .distinct()
        .where(User.trial == 1)
        .where(Payment.amount > 0)
        .where(Payment.status == "success")
        .where(Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED))
        .where(~Payment.user_id.in_(sub_active))
    )

    result = await session.execute(stmt)
    return [
        (int(user_id), datetime.fromtimestamp(int(expiry_ms) / 1000, UTC))
        for user_id, expiry_ms in result.all()
        if expiry_ms is not None
    ]
