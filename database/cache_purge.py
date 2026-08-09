from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete


_PENDING = "pending_cache_purge"


def defer_purge(session: AsyncSession | None, *keys: str) -> bool:
    """Откладывает сброс ключей до коммита. Сброс до него бесполезен: читатель успеет
    наполнить кеш ещё не закоммиченными данными, и они переживут коммит на весь TTL."""
    if session is None:
        return False
    try:
        info = session.info
    except Exception:
        return False
    pending = info.get(_PENDING)
    if pending is None:
        pending = set()
        info[_PENDING] = pending
    pending.update(k for k in keys if k)
    return True


async def flush_purges(session: AsyncSession | None) -> None:
    """Сбрасывает отложенные ключи. Вызывается после успешного коммита."""
    if session is None:
        return
    try:
        pending = session.info.pop(_PENDING, None)
    except Exception:
        return
    for key in pending or ():
        try:
            await cache_delete(key)
        except Exception:
            continue
