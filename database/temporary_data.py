from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TemporaryData
from logger import logger


async def create_temporary_data(
    session: AsyncSession, tg_id: int, state: str, data: dict
):
    try:
        stmt = (
            insert(TemporaryData)
            .values(tg_id=tg_id, state=state, data=data, updated_at=datetime.utcnow())
            .on_conflict_do_update(
                index_elements=[TemporaryData.tg_id],
                set_={"state": state, "data": data, "updated_at": datetime.utcnow()},
            )
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"📝 Временные данные сохранены для {tg_id}")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при сохранении временных данных для {tg_id}: {e}")
        await session.rollback()


async def get_temporary_data(session: AsyncSession, tg_id: int) -> dict | None:
    stmt = select(TemporaryData).where(TemporaryData.tg_id == tg_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row:
        return {"state": row.state, "data": row.data}
    return None


async def clear_temporary_data(session: AsyncSession, tg_id: int):
    await session.execute(delete(TemporaryData).where(TemporaryData.tg_id == tg_id))
    await session.commit()
    logger.info(f"🗑 Временные данные очищены для {tg_id}")
