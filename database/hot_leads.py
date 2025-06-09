from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Payment


async def get_hot_leads(session: AsyncSession):
    """
    Возвращает пользователей, у которых есть успешные оплаты, но нет активных ключей.
    """
    subquery = (
        select(Key.tg_id)
        .where(Key.expiry_time > func.extract("epoch", func.now()) * 1000)
        .distinct()
    )

    stmt = (
        select(Payment.tg_id)
        .distinct()
        .where(Payment.amount > 0)
        .where(~Payment.tg_id.in_(subquery))
    )

    result = await session.execute(stmt)
    return [row.tg_id for row in result]
