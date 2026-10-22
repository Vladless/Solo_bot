import time

from sqlalchemy import text

from database.db import async_session_maker


async def increment_rate_limit_counter(key: str, window_sec: int) -> int:
    """Атомарно увеличивает общий счётчик запросов в фиксированном окне PostgreSQL."""
    now = int(time.time())
    window_start = (now // max(1, window_sec)) * max(1, window_sec)
    async with async_session_maker() as session:
        res = await session.execute(
            text(
                """
                INSERT INTO rate_limit_counters (bucket, window_start, count)
                VALUES (:b, :w, 1)
                ON CONFLICT (bucket, window_start)
                DO UPDATE SET count = rate_limit_counters.count + 1
                RETURNING count
                """
            ),
            {"b": key[:255], "w": window_start},
        )
        value = res.scalar()
        await session.commit()
        return int(value or 1)
