from __future__ import annotations

import time

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import _identity_from_cookie


async def _db_rate_incr(key: str, window_sec: int) -> int:
    """Распределённый (общий для всех реплик) fallback-счётчик в Postgres.
    Используется, когда Redis недоступен. Фиксированное окно."""
    from database import async_session_maker

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


async def enforce_rate_limit(
    request: Request,
    session: AsyncSession,
    *,
    bucket: str,
    max_per_window: int,
    window_sec: int,
    identity_aware: bool = True,
) -> None:
    try:
        from core.rate_limit import check_and_increment
        from core.redis_cache import cache_incr_checked
    except Exception:
        return

    owner = "anon"
    if identity_aware:
        try:
            identity = await _identity_from_cookie(session, request)
            if identity is not None and getattr(identity, "id", None):
                owner = f"id:{identity.id}"
        except Exception:
            pass

    if owner == "anon":
        try:
            from api.v2.routes.auth._common import _client_ip

            ip = _client_ip(request) or "unknown"
        except Exception:
            ip = (request.client.host if request.client else "") or "unknown"
        owner = f"ip:{ip}"

    key = f"rl:{bucket}:{owner}"
    try:
        count, redis_ok = await cache_incr_checked(key, window_sec)
        if not redis_ok:
            try:
                count = await _db_rate_incr(key, window_sec)
            except Exception:
                count = check_and_increment(key, max_per_window, window_sec)
    except Exception:
        return

    if count > max_per_window:
        raise HTTPException(status_code=429, detail="Слишком много запросов, подождите и попробуйте снова")


def rate_limit_dependency(*, bucket: str, max_per_window: int, window_sec: int):
    from fastapi import Depends

    from api.depends import get_session

    async def _dep(request: Request, session: AsyncSession = Depends(get_session)) -> None:
        await enforce_rate_limit(
            request,
            session,
            bucket=bucket,
            max_per_window=max_per_window,
            window_sec=window_sec,
        )

    return _dep
