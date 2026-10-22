from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import _identity_from_cookie
from database.rate_limit import increment_rate_limit_counter


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
                count = await increment_rate_limit_counter(key, window_sec)
            except Exception:
                count = check_and_increment(key, max_per_window, window_sec)
    except Exception:
        return

    if count > max_per_window:
        raise HTTPException(status_code=429, detail="Слишком много запросов, подождите и попробуйте снова")
