from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import resolve_user_optional
from database.models import TemporaryData
from logger import logger


async def create_temporary_data(session: AsyncSession, legacy_user_ref: int, state: str, data: dict):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        raise ValueError(f"user not found for temporary data: {legacy_user_ref}")
    ins = insert(TemporaryData).values(
        user_id=u.id,
        tg_id=u.tg_id,
        state=state,
        data=data,
        updated_at=datetime.utcnow(),
    )
    stmt = ins.on_conflict_do_update(
        index_elements=[TemporaryData.user_id],
        set_={
            "state": state,
            "data": data,
            "updated_at": datetime.utcnow(),
            "tg_id": ins.excluded.tg_id,
        },
    )
    await session.execute(stmt)
    logger.info(f"📝 Временные данные сохранены для user_id={u.id}")


async def get_temporary_data(session: AsyncSession, legacy_user_ref: int) -> dict | None:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is not None:
        stmt = select(TemporaryData).where(
            or_(
                TemporaryData.user_id == u.id,
                TemporaryData.tg_id == u.tg_id,
            )
        )
    else:
        stmt = select(TemporaryData).where(TemporaryData.tg_id == legacy_user_ref)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row:
        return {"state": row.state, "data": row.data}
    return None


async def clear_temporary_data(session: AsyncSession, legacy_user_ref: int):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is not None:
        await session.execute(
            delete(TemporaryData).where(
                or_(
                    TemporaryData.user_id == u.id,
                    TemporaryData.tg_id == u.tg_id,
                )
            )
        )
    else:
        await session.execute(delete(TemporaryData).where(TemporaryData.tg_id == legacy_user_ref))
    logger.info(f"🗑 Временные данные очищены для {legacy_user_ref}")
