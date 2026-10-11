from datetime import UTC, datetime

from core.bootstrap import NOTIFICATIONS_CONFIG
from settings.config import RENEW_BUTTON_BEFORE_DAYS


def renewal_available_from_ms(expiry_ms: int) -> int:
    """Возвращает начало окна продления."""
    renew_before_days = int(NOTIFICATIONS_CONFIG.get("RENEW_BUTTON_BEFORE_DAYS", RENEW_BUTTON_BEFORE_DAYS))
    return int(expiry_ms) - renew_before_days * 86_400_000


def is_renewal_available(expiry_ms: int, *, now_ms: int | None = None) -> bool:
    """Проверяет доступность обычного продления."""
    if not expiry_ms:
        return True
    if now_ms is None:
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
    return int(now_ms) >= renewal_available_from_ms(expiry_ms)
