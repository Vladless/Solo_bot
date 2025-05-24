from datetime import datetime

from sqlalchemy import insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment
from logger import logger


async def add_payment(
    session: AsyncSession, tg_id: int, amount: float, payment_system: str
):
    try:
        stmt = insert(Payment).values(
            tg_id=tg_id,
            amount=amount,
            payment_system=payment_system,
            status="success",
            created_at=datetime.utcnow(),
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(
            f"✅ Успешно добавлен платёж: {tg_id}, {amount}₽ через {payment_system}"
        )
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при добавлении платежа: {e}")
        await session.rollback()
        raise


async def get_last_payments(session: AsyncSession, tg_id: int, limit: int = 3):
    try:
        result = await session.execute(
            select(Payment)
            .where(Payment.tg_id == tg_id)
            .order_by(Payment.created_at.desc())
            .limit(limit)
        )
        payments = result.scalars().all()
        logger.info(
            f"✅ Получены последние платежи пользователя {tg_id}, всего: {len(payments)}"
        )
        return [dict(p.__dict__) for p in payments]
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при получении платежей пользователя {tg_id}: {e}")
        return []
