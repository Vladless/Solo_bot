from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import BlockedUser


async def create_blocked_user(session: AsyncSession, tg_id: int):
    stmt = (
        insert(BlockedUser)
        .values(tg_id=tg_id)
        .on_conflict_do_nothing(index_elements=[BlockedUser.tg_id])
    )
    await session.execute(stmt)
    await session.commit()
