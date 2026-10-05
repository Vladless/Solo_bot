from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_get, cache_set
from database.models import Setting


_KEY = "SITE_INITIALIZED"
_CACHE_KEY = "site_state:initialized"
_CACHE_TTL_SEC = 300


async def is_site_initialized(session: AsyncSession) -> bool:
    cached = await cache_get(_CACHE_KEY)
    if isinstance(cached, bool):
        return cached
    result = await session.execute(select(Setting).where(Setting.key == _KEY))
    setting = result.scalar_one_or_none()
    value = bool(setting and setting.value is True)
    await cache_set(_CACHE_KEY, value, _CACHE_TTL_SEC)
    return value


async def mark_site_initialized(session: AsyncSession) -> None:
    """Идемпотентно выставляет флаг инициализации сайта."""
    if await cache_get(_CACHE_KEY) is True:
        return
    result = await session.execute(select(Setting).where(Setting.key == _KEY))
    setting = result.scalar_one_or_none()
    first_init = False
    if setting is None:
        session.add(Setting(key=_KEY, value=True, description="Сайт прошёл первую настройку админом"))
        first_init = True
    elif setting.value is not True:
        setting.value = True
        first_init = True
    if first_init:
        await cache_delete(_CACHE_KEY)
    else:
        await cache_set(_CACHE_KEY, True, _CACHE_TTL_SEC)
    if first_init:
        try:
            from database.web_default_seed import seed_default_site

            await seed_default_site(session)
        except Exception:
            pass


async def reset_site_initialized(session: AsyncSession) -> None:
    """Сброс флага — вызывается при полном ресете сайта через TG-бот."""
    result = await session.execute(select(Setting).where(Setting.key == _KEY))
    setting = result.scalar_one_or_none()
    if setting is not None:
        setting.value = False
    await cache_delete(_CACHE_KEY)
