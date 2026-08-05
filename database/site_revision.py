from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_delete_pattern, cache_get, cache_set
from database.models import Setting


_KEY = "CONTENT_REVISION"
_CACHE_KEY = "site_revision:value"
_CACHE_TTL_SEC = 30


async def get_site_revision(session: AsyncSession) -> int:
    cached = await cache_get(_CACHE_KEY)
    if isinstance(cached, int):
        return cached
    result = await session.execute(select(Setting).where(Setting.key == _KEY))
    setting = result.scalar_one_or_none()
    if setting is None:
        await cache_set(_CACHE_KEY, 0, _CACHE_TTL_SEC)
        return 0
    try:
        value = int(setting.value or 0)
    except (TypeError, ValueError):
        value = 0
    await cache_set(_CACHE_KEY, value, _CACHE_TTL_SEC)
    return value


async def bump_site_revision(session: AsyncSession) -> int:
    result = await session.execute(select(Setting).where(Setting.key == _KEY))
    setting = result.scalar_one_or_none()
    if setting is None:
        session.add(Setting(key=_KEY, value=1))
        await cache_delete(_CACHE_KEY)
        await cache_delete_pattern("web_page:*")
        return 1
    try:
        current = int(setting.value or 0)
    except (TypeError, ValueError):
        current = 0
    setting.value = current + 1
    await cache_delete(_CACHE_KEY)
    await cache_delete_pattern("web_page:*")
    return current + 1
