from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_delete_pattern
from logger import logger


_PENDING = "pending_cache_purge"
_PENDING_PATTERNS = "pending_cache_purge_patterns"


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
    """Сбрасывает отложенный кеш после успешного коммита."""
    info = _pending_store(session)
    if info is None:
        return
    pending = info.pop(_PENDING, None)
    for key in pending or ():
        try:
            await cache_delete(key)
        except Exception as exc:
            logger.debug("[Cache] Purge after commit failed: {}", exc)
    for pattern in info.pop(_PENDING_PATTERNS, ()):
        try:
            await cache_delete_pattern(pattern)
        except Exception as exc:
            logger.debug("[Cache] Pattern purge after commit failed: {}", exc)


def defer_purge_patterns(session: AsyncSession | None, *patterns: str) -> bool:
    info = _pending_store(session)
    if info is None:
        return False
    info.setdefault(_PENDING_PATTERNS, set()).update(patterns)
    return True


def is_purge_pending(session: AsyncSession | None, key: str) -> bool:
    info = _pending_store(session)
    return info is not None and key in info.get(_PENDING, ())
