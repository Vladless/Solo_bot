from aiohttp import web

from core.redis_cache import cache_delete, cache_get, cache_incr, cache_key, cache_set
from logger import logger
from settings.cache_config import (
    WEBHOOK_ABUSE_BLOCK_TTL_SEC,
    WEBHOOK_ABUSE_FAIL_THRESHOLD,
    WEBHOOK_ABUSE_FAIL_WINDOW_SEC,
)


def get_webhook_client_ip(request: web.Request) -> str:
    """IP клиента: X-Forwarded-For (первый) или X-Real-IP, иначе request.remote."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    real = request.headers.get("X-Real-IP")
    if real:
        return real.strip() or "unknown"
    if request.remote:
        s = str(request.remote)
        if "%" in s:
            s = s.split("%")[0]
        if ":" in s:
            s = s.rsplit(":", 1)[0]
        return s or "unknown"
    return "unknown"


async def is_webhook_ip_blocked(ip: str) -> bool:
    """True, если IP временно заблокирован из‑за множества невалидных подписей."""
    if not ip or ip == "unknown":
        return False
    try:
        block_key = cache_key("webhook_abuse_block", ip)
        return (await cache_get(block_key)) is not None
    except Exception:
        return False


async def record_webhook_signature_failure(ip: str) -> None:
    """Увеличивает счётчик неудачных проверок подписи для IP; при превышении порога блокирует IP."""
    if not ip or ip == "unknown":
        return
    try:
        fail_key = cache_key("webhook_abuse_fail", ip)
        count = await cache_incr(fail_key, WEBHOOK_ABUSE_FAIL_WINDOW_SEC)
        if count >= WEBHOOK_ABUSE_FAIL_THRESHOLD:
            block_key = cache_key("webhook_abuse_block", ip)
            await cache_set(block_key, 1, WEBHOOK_ABUSE_BLOCK_TTL_SEC)
            await cache_delete(fail_key)
    except Exception as e:
        logger.warning("[WebhookAbuse] Ошибка записи fail-счётчика для IP={}: {}", ip, e)
