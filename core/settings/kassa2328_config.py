import os

from decimal import Decimal, InvalidOperation

from settings import config


def _setting_text(name: str, default: str = "") -> str:
    """Читает настройку кассы из окружения или конфига."""
    value = os.environ.get(name, getattr(config, name, default))
    return str(value if value is not None else default).strip()


def _setting_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    """Читает целое число в допустимом диапазоне."""
    try:
        value = int(_setting_text(name, str(default)))
    except (TypeError, ValueError, OverflowError):
        return default
    return value if minimum <= value <= maximum else default


def _setting_amount(name: str, default: str) -> Decimal:
    """Читает положительную конечную сумму."""
    try:
        value = Decimal(_setting_text(name, default))
    except (InvalidOperation, ValueError):
        return Decimal(default)
    return value if value.is_finite() and value > 0 else Decimal(default)


KASSA2328_PROJECT_UUID = _setting_text("KASSA2328_PROJECT_UUID")
KASSA2328_API_KEY = _setting_text("KASSA2328_API_KEY")
KASSA2328_API_BASE_URL = "https://api.2328.io/api"
KASSA2328_WEBHOOK_PATH = "/2328/webhook"
KASSA2328_CALLBACK_URL = _setting_text(
    "KASSA2328_CALLBACK_URL",
    f"{str(getattr(config, 'WEBHOOK_HOST', '') or '').strip().rstrip('/')}{KASSA2328_WEBHOOK_PATH}",
)
KASSA2328_RETURN_URL = _setting_text("KASSA2328_RETURN_URL", str(getattr(config, "MAINURL", "") or ""))
KASSA2328_INVOICE_CURRENCY = _setting_text("KASSA2328_INVOICE_CURRENCY", "RUB").upper()
KASSA2328_TTL_SECONDS = _setting_integer("KASSA2328_TTL_SECONDS", 3600, 300, 86400)
KASSA2328_REQUEST_TIMEOUT = _setting_integer("KASSA2328_REQUEST_TIMEOUT", 25, 1, 300)
KASSA2328_MIN_AMOUNT_RUB = _setting_amount("KASSA2328_MIN_AMOUNT_RUB", "1.00")
KASSA2328_MAX_AMOUNT_RUB = _setting_amount("KASSA2328_MAX_AMOUNT_RUB", "1000000.00")
