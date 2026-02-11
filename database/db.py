import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from config import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE


CONCURRENT_UPDATES_LIMIT = DB_POOL_SIZE + DB_MAX_OVERFLOW
MAX_UPDATE_AGE_SEC = 28

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_size=DB_POOL_SIZE,
    max_overflow=DB_MAX_OVERFLOW,
    pool_timeout=60,
    pool_pre_ping=True,
    pool_recycle=300,
)

async_session_maker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

Base = declarative_base()

WARM_POOL_COUNT = 10


async def warm_pool() -> None:
    """
    Прогревает пул соединений при старте.
    """

    async def _one() -> None:
        async with async_session_maker() as session:
            await session.execute(text("SELECT 1"))

    count = min(WARM_POOL_COUNT, DB_POOL_SIZE)
    if count <= 0:
        return
    await asyncio.gather(*[asyncio.create_task(_one()) for _ in range(count)])
