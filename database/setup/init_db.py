from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from database import db
from database.migrations.schema_upgrade import apply_all_migrations
from database.models import Admin, Base, User
from settings.config import ADMIN_ID, DATABASE_URL


async def run_schema_setup(*, create_all: bool = True) -> None:
    """Schema migrations via a dedicated engine (no runtime command_timeout)."""
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.begin() as conn:
            if create_all:
                await conn.run_sync(Base.metadata.create_all)
            await apply_all_migrations(conn)
    finally:
        await engine.dispose()


async def init_db(run_migrations: bool = True):
    if run_migrations:
        await run_schema_setup(create_all=True)

    async with db.async_session_maker() as session:
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
