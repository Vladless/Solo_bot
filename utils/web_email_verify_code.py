import hmac

from core.redis_cache import (
    cache_delete,
    cache_get,
    cache_incr,
    cache_key,
    cache_set,
    cache_setnx,
    redis_connection_ok,
)
from settings.config import LOGIN_CODE_TTL_SEC


_RESEND_COOLDOWN_SEC = 60.0
_IP_WINDOW_SEC = 3600.0
_IP_MAX_SENDS = 20
_EMAIL_WINDOW_SEC = 3600.0
_EMAIL_MAX_SENDS = 5
_EMAIL_VERIFY_WINDOW_SEC = 600.0
_EMAIL_MAX_VERIFY_ATTEMPTS = 10


def _code_key(email_norm: str) -> str:
    return cache_key("web_email_verify_code", email_norm)


def _cooldown_key(email_norm: str) -> str:
    return cache_key("web_email_verify_cooldown", email_norm)


def _ip_key(ip: str) -> str:
    return cache_key("web_email_verify_send_ip", ip)


def _email_send_key(email_norm: str) -> str:
    return cache_key("web_email_verify_sends", email_norm)


def _email_verify_key(email_norm: str) -> str:
    return cache_key("web_email_verify_attempts", email_norm)


async def redis_ready() -> bool:
    return await redis_connection_ok()


async def try_consume_ip_send_budget(ip: str) -> bool:
    if not ip:
        return True
    n = await cache_incr(_ip_key(ip), _IP_WINDOW_SEC)
    return n <= _IP_MAX_SENDS


async def try_consume_email_send_budget(email_norm: str) -> bool:
    if not email_norm:
        return True
    n = await cache_incr(_email_send_key(email_norm), _EMAIL_WINDOW_SEC)
    return n <= _EMAIL_MAX_SENDS


async def try_consume_verify_budget(email_norm: str) -> bool:
    if not email_norm:
        return True
    n = await cache_incr(_email_verify_key(email_norm), _EMAIL_VERIFY_WINDOW_SEC)
    return n <= _EMAIL_MAX_VERIFY_ATTEMPTS


async def try_acquire_resend_cooldown(email_norm: str) -> bool:
    return await cache_setnx(_cooldown_key(email_norm), 1, _RESEND_COOLDOWN_SEC)


async def store_code(email_norm: str, code: str) -> bool:
    return await cache_set(_code_key(email_norm), code, float(LOGIN_CODE_TTL_SEC))


async def delete_code(email_norm: str) -> None:
    await cache_delete(_code_key(email_norm))


async def verify_and_consume_code(email_norm: str, code: str) -> bool:
    key = _code_key(email_norm)
    stored = await cache_get(key)
    if not isinstance(stored, str):
        return False
    if not hmac.compare_digest(stored.strip(), (code or "").strip()):
        return False
    await cache_delete(key)
    return True
