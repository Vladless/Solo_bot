from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete


_PENDING = "pending_cache_purge"


def _pending_store(session) -> dict | None:
    """Хранилище отложенных ключей или None, если сессия отпущена."""
    if session is None:
        return None
    try:
        info = session.info
    except Exception:
        return None
    return info if isinstance(info, dict) else None


def defer_purge(session: AsyncSession | None, *keys: str) -> bool:
    """Откладывает сброс ключей кеша до коммита."""
    info = _pending_store(session)
    if info is None:
        return False
    try:
        pending = info.get(_PENDING)
        if not isinstance(pending, set):
            pending = set()
            info[_PENDING] = pending
        pending.update(k for k in keys if k)
    except Exception:
        return False
    return True


async def flush_purges(session: AsyncSession | None) -> None:
    """Сбрасывает отложенные ключи. Вызывается после успешного коммита."""
    info = _pending_store(session)
    if info is None:
        return
    pending = info.pop(_PENDING, None)
    for key in pending or ():
        try:
            await cache_delete(key)
        except Exception:
            continue
