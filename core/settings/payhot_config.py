import os

from decimal import Decimal, InvalidOperation

from settings import config


def _text(name: str, default: str = "") -> str:
    """Читает настройку PayHOT из окружения или конфига."""
    value = os.environ.get(name, getattr(config, name, default))
    return str(value if value is not None else default).strip()


def _amount(name: str, default: str) -> Decimal:
    """Читает положительную сумму с точностью до копейки."""
    try:
        value = Decimal(_text(name, default))
        if value.is_finite() and value > 0 and value == value.quantize(Decimal("0.01")):
            return value
    except (InvalidOperation, ValueError):
        pass
    return Decimal(default)


def _timeout() -> int:
    """Читает ограничение времени запроса к PayHOT."""
    try:
        value = int(_text("PAYHOT_REQUEST_TIMEOUT", "25"))
        if 1 <= value <= 120:
            return value
    except ValueError:
        pass
    return 25


PAYHOT_API_KEY = _text("PAYHOT_API_KEY")
PAYHOT_WEBHOOK_SECRET = _text("PAYHOT_WEBHOOK_SECRET")
PAYHOT_PREVIOUS_WEBHOOK_SECRET = _text("PAYHOT_PREVIOUS_WEBHOOK_SECRET")
PAYHOT_API_BASE_URL = _text("PAYHOT_API_BASE_URL", "https://app.pay.hot/api/v2").rstrip("/")
PAYHOT_WEBHOOK_PATH = _text("PAYHOT_WEBHOOK_PATH", "/payhot/webhook")
PAYHOT_REQUEST_TIMEOUT = _timeout()
PAYHOT_MIN_AMOUNT_RUB = _amount("PAYHOT_MIN_AMOUNT_RUB", "0.01")
PAYHOT_MAX_AMOUNT_RUB = _amount("PAYHOT_MAX_AMOUNT_RUB", "1000000.00")
