import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from settings.cache_config import UPDATE_STALE_AGE_SEC
from settings.config import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE, USE_PGBOUNCER


CONCURRENT_UPDATES_LIMIT = DB_POOL_SIZE + DB_MAX_OVERFLOW
MAX_UPDATE_AGE_SEC = UPDATE_STALE_AGE_SEC

_db_url = DATABASE_URL
_connect_args = {}
if USE_PGBOUNCER and "+asyncpg" in DATABASE_URL:
    _connect_args["prepared_statement_cache_size"] = 0
    sep = "&" if "?" in _db_url else "?"
    _db_url = f"{_db_url}{sep}prepared_statement_cache_size=0"

_pool_recycle = 60 if USE_PGBOUNCER else 300

_QUERY_TIMEOUT_SEC = 30

if "+asyncpg" in _db_url:
    _connect_args.setdefault("command_timeout", _QUERY_TIMEOUT_SEC)
    _connect_args.setdefault("timeout", _QUERY_TIMEOUT_SEC)


def _create_engine():
    return create_async_engine(
        _db_url,
        echo=False,
        future=True,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=60,
        pool_pre_ping=True,
        pool_recycle=_pool_recycle,
        connect_args=_connect_args,
    )


engine = _create_engine()

async_session_maker = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

Base = declarative_base()

WARM_POOL_COUNT = 10


def reset_async_db_engine() -> None:
    global engine
    engine = _create_engine()
    async_session_maker.configure(bind=engine)


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
