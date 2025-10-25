from datetime import datetime

from sqlalchemy import select

from config import ADMIN_ID
from database.db import async_session_maker, engine
from database.models import Admin, Base, User
from database.tariffs import initialize_all_tariff_weights


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.tg_id == 0))
        if not result.scalar_one_or_none():
            session.add(
                User(
                    tg_id=0,
                    username="system",
                    first_name="System",
                    is_bot=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )

        for tg_id in ADMIN_ID:
            result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
            if not result.scalar_one_or_none():
                session.add(
                    Admin(
                        tg_id=tg_id, role="superadmin", description="Imported from config", added_at=datetime.utcnow()
                    )
                )
        await session.commit()

        await initialize_all_tariff_weights(session)
