import httpx

from logger import logger
from settings.config import TURNSTILE_SECRET_KEY


_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_enabled() -> bool:
    return bool(TURNSTILE_SECRET_KEY)


async def verify_turnstile_token(token: str | None, remote_ip: str | None = None) -> bool:
    if not TURNSTILE_SECRET_KEY:
        return True

    if not token or token == "__turnstile_disabled__":
        logger.warning("[Turnstile] токен не предоставлен")
        return False

    try:
        payload: dict[str, str] = {
            "secret": TURNSTILE_SECRET_KEY,
            "response": token,
        }
        if remote_ip:
            payload["remoteip"] = remote_ip

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(_VERIFY_URL, data=payload)
            result = resp.json()

        success = result.get("success", False)
        if not success:
            codes = result.get("error-codes", [])
            logger.warning("[Turnstile] верификация не пройдена: {}", codes)
        return bool(success)
    except Exception as exc:
        logger.error("[Turnstile] ошибка проверки: {}", exc)
        return False
