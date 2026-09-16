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


RESEND_COOLDOWN_SEC = 60.0
IP_WINDOW_SEC = 3600.0
EMAIL_WINDOW_SEC = 3600.0
EMAIL_MAX_SENDS = 5
VERIFY_WINDOW_SEC = 600.0
MAX_VERIFY_ATTEMPTS = 10


def normalize_email(value: str) -> str:
    """Почта в каноничном виде: без пробелов и в нижнем регистре."""
    return (value or "").strip().lower()


class EmailCodeFlow:
    """Одноразовый код на почту: хранение, лимиты отправок и проверок, антиповтор."""

    def __init__(self, namespace: str, *, ip_max_sends: int = 40) -> None:
        self.namespace = namespace
        self.ip_max_sends = ip_max_sends

    def _key(self, suffix: str, value: str) -> str:
        return cache_key(f"{self.namespace}_{suffix}", value)

    async def redis_ready(self) -> bool:
        return await redis_connection_ok()

    async def try_consume_ip_budget(self, ip: str) -> bool:
        if not ip:
            return True
        return await cache_incr(self._key("send_ip", ip), IP_WINDOW_SEC) <= self.ip_max_sends

    async def try_consume_email_send_budget(self, email_norm: str) -> bool:
        if not email_norm:
            return True
        return await cache_incr(self._key("sends", email_norm), EMAIL_WINDOW_SEC) <= EMAIL_MAX_SENDS

    async def try_consume_verify_budget(self, email_norm: str) -> bool:
        if not email_norm:
            return True
        return await cache_incr(self._key("verify", email_norm), VERIFY_WINDOW_SEC) <= MAX_VERIFY_ATTEMPTS

    async def try_acquire_cooldown(self, email_norm: str) -> bool:
        return await cache_setnx(self._key("cooldown", email_norm), 1, RESEND_COOLDOWN_SEC)

    async def release_cooldown(self, email_norm: str) -> None:
        await cache_delete(self._key("cooldown", email_norm))

    async def store_code(self, email_norm: str, code: str) -> bool:
        return await cache_set(self._key("code", email_norm), code, float(LOGIN_CODE_TTL_SEC))

    async def delete_code(self, email_norm: str) -> None:
        await cache_delete(self._key("code", email_norm))

    async def verify_and_consume_code(self, email_norm: str, code: str) -> bool:
        key = self._key("code", email_norm)
        stored = await cache_get(key)
        if not isinstance(stored, str):
            return False
        if not hmac.compare_digest(stored.strip(), (code or "").strip()):
            return False
        await cache_delete(key)
        return True


login_codes = EmailCodeFlow("web_login")
email_link_codes = EmailCodeFlow("web_email_link")
email_verify_codes = EmailCodeFlow("web_email_verify", ip_max_sends=20)
password_reset_codes = EmailCodeFlow("web_pwd_reset")
