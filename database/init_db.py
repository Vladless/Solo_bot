from database.db import engine, async_session_maker
from database.models import Base, Admin
from sqlalchemy import select
from config import ADMIN_ID
from datetime import datetime

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        for tg_id in ADMIN_ID:
            result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
            if not result.scalar_one_or_none():
                session.add(Admin(
                    tg_id=tg_id,
                    role="superadmin",
                    description="Imported from config",
                    added_at=datetime.utcnow()
                ))
        await session.commit()
