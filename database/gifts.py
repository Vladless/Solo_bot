from datetime import datetime

from sqlalchemy import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Gift
from logger import logger


async def store_gift_link(
    session: AsyncSession,
    gift_id: str,
    sender_tg_id: int,
    selected_months: int,
    expiry_time: datetime,
    gift_link: str,
    tariff_id: int | None = None,
):
    try:
        stmt = insert(Gift).values(
            gift_id=gift_id,
            sender_tg_id=sender_tg_id,
            recipient_tg_id=None,
            selected_months=selected_months,
            expiry_time=expiry_time,
            gift_link=gift_link,
            created_at=datetime.utcnow(),
            is_used=False,
            tariff_id=tariff_id,
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"🎁 Подарок {gift_id} сохранён (tariff_id={tariff_id})")
        return True
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при сохранении подарка {gift_id}: {e}")
        await session.rollback()
        return False
