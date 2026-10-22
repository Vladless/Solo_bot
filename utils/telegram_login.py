import hashlib
import hmac
import time

from logger import logger


def verify_telegram_login(
    payload: dict,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
) -> bool:
    """Проверяет подпись и свежесть данных виджета входа Telegram."""
    if not payload or not bot_token:
        return False
    received_hash = payload.get("hash")
    if not received_hash:
        return False
    auth_date = payload.get("auth_date")
    if auth_date is None:
        return False
    try:
        if int(auth_date) < time.time() - max_age_seconds:
            return False
    except (TypeError, ValueError):
        return False

    check_parts = sorted((k, v) for k, v in payload.items() if k != "hash" and v is not None)
    data_check_string = "\n".join(f"{k}={v}" for k, v in check_parts)

    secret_key = hashlib.sha256(bot_token.encode()).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    return hmac.compare_digest(computed, received_hash)


def verify_webapp_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
) -> dict | None:
    """Проверяет подпись данных Telegram WebApp и возвращает данные пользователя или None."""
    import json

    from urllib.parse import parse_qs

    if not init_data or not bot_token:
        return None

    parsed = parse_qs(init_data, keep_blank_values=True)
    received_hash = parsed.get("hash", [""])[0]
    if not received_hash:
        logger.warning("[Site:Auth] Telegram-приложение прислало данные входа без подписи")
        return None

    auth_date_str = parsed.get("auth_date", [""])[0]
    try:
        auth_date = int(auth_date_str)
    except (TypeError, ValueError):
        logger.warning("[Site:Auth] В данных входа из Telegram нет отметки времени")
        return None
    if auth_date < time.time() - max_age_seconds:
        logger.warning(
            "[Site:Auth] Данные входа из Telegram просрочены: им {} с при лимите {} с",
            int(time.time() - auth_date),
            max_age_seconds,
        )
        return None

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    def _hmac_ok(exclude: set[str]) -> bool:
        pairs = [f"{k}={parsed[k][0]}" for k in sorted(parsed.keys()) if k not in exclude]
        computed = hmac.new(secret_key, "\n".join(pairs).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed, received_hash)

    if not (_hmac_ok({"hash"}) or _hmac_ok({"hash", "signature"})):
        logger.warning("[Site:Auth] Подпись данных входа из Telegram не совпала")
        return None

    user_raw = parsed.get("user", [""])[0]
    user_id = None
    if user_raw:
        try:
            user_data = json.loads(user_raw)
            user_id = user_data.get("id")
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "user_id": user_id,
        "auth_date": auth_date,
        "user_raw": user_raw,
    }
