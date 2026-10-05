from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from database import db
from database.migrations.autogenerate import SchemaReport, apply_model_changes, log_report
from database.migrations.schema_upgrade import apply_all_migrations
from database.models import Admin, Base, User
from database.setup.module_models import import_module_models
from settings.config import ADMIN_ID, DATABASE_URL


async def run_schema_setup() -> None:
    """Один шаг обновления схемы: перенос данных старых баз, затем автогенерация alembic по моделям.

    Порядок важен: внешние ключи новых таблиц смотрят на `users.id`, а на базе старого клиента эту
    колонку заводит перенос. Файлов ревизий нет — разница считается от фактической схемы.
    """
    import_module_models()
    engine = create_async_engine(DATABASE_URL)
    try:
        report = SchemaReport()
        async with engine.begin() as conn:
            await apply_all_migrations(conn)
            await apply_model_changes(conn, Base.metadata, report)
    finally:
        await engine.dispose()
    log_report(report)


async def init_db(run_migrations: bool = True):
    if run_migrations:
        await run_schema_setup()

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
