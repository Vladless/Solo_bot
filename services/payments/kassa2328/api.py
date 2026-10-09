import base64
import hashlib
import hmac
import json
import re

from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import aiohttp

from core.settings import kassa2328_config as config
from settings import texts


class PaymentError(ValueError):
    pass


def encode_json(data: dict) -> bytes:
    """Кодирует тело запроса без изменения порядка полей."""
    encoded = json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029").encode("utf-8")


def sign_body(body: bytes, api_key: str) -> str:
    """Подписывает точные байты тела запроса."""
    return hmac.new(api_key.encode("utf-8"), base64.b64encode(body), hashlib.sha256).hexdigest()


def verify_webhook(data: dict) -> bool:
    """Проверяет подпись уведомления 2328."""
    key = config.KASSA2328_API_KEY
    if not key or not isinstance(data, dict):
        return False
    signature = data.get("sign")
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False
    try:
        unsigned = {name: value for name, value in data.items() if name != "sign"}
        expected = sign_body(encode_json(unsigned), key)
    except (TypeError, ValueError, UnicodeError):
        return False
    return hmac.compare_digest(signature, expected)


def positive_decimal(value) -> Decimal:
    """Проверяет положительную конечную сумму."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PaymentError(texts.KASSA2328_SERVICE_INVALID_AMOUNT) from exc
    if not result.is_finite() or result <= 0:
        raise PaymentError(texts.KASSA2328_SERVICE_POSITIVE_AMOUNT_REQUIRED)
    return result


def rub_amount(value) -> Decimal:
    """Проверяет точность и пределы рублёвого пополнения."""
    amount = positive_decimal(value)
    minimum = positive_decimal(config.KASSA2328_MIN_AMOUNT_RUB)
    maximum = positive_decimal(config.KASSA2328_MAX_AMOUNT_RUB)
    if not minimum <= amount <= maximum:
        raise PaymentError(texts.KASSA2328_SERVICE_AMOUNT_RANGE.format(minimum=minimum, maximum=maximum))
    try:
        rounded = amount.quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise PaymentError(texts.KASSA2328_SERVICE_INVALID_PRECISION) from exc
    if amount != rounded:
        raise PaymentError(texts.KASSA2328_SERVICE_TWO_DECIMALS_REQUIRED)
    return rounded


def validate_config() -> None:
    """Проверяет настройки создания счетов."""
    if not config.KASSA2328_PROJECT_UUID or not config.KASSA2328_API_KEY:
        raise PaymentError(texts.KASSA2328_SERVICE_PROJECT_CREDENTIALS_REQUIRED)
    callback = urlsplit(config.KASSA2328_CALLBACK_URL)
    if (
        callback.scheme != "https"
        or not callback.hostname
        or callback.query
        or callback.fragment
        or callback.path != config.KASSA2328_WEBHOOK_PATH
    ):
        raise PaymentError(texts.KASSA2328_SERVICE_PUBLIC_WEBHOOK_REQUIRED)
    if config.KASSA2328_INVOICE_CURRENCY not in {"RUB", "USD"}:
        raise PaymentError(texts.KASSA2328_SERVICE_INVALID_CURRENCY)
    if not 300 <= config.KASSA2328_TTL_SECONDS <= 86400:
        raise PaymentError(texts.KASSA2328_SERVICE_INVALID_TTL)
    if config.KASSA2328_MIN_AMOUNT_RUB > config.KASSA2328_MAX_AMOUNT_RUB:
        raise PaymentError(texts.KASSA2328_SERVICE_INVALID_LIMITS)


async def api_request(path: str, payload: dict) -> dict:
    """Отправляет подписанный запрос платёжной кассе 2328."""
    if not config.KASSA2328_PROJECT_UUID or not config.KASSA2328_API_KEY:
        raise PaymentError(texts.KASSA2328_SERVICE_CREDENTIALS_REQUIRED)
    body = encode_json(payload)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "SoloBot/6 2328",
        "project": config.KASSA2328_PROJECT_UUID,
        "sign": sign_body(body, config.KASSA2328_API_KEY),
    }
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=config.KASSA2328_REQUEST_TIMEOUT)
        ) as client:
            async with client.post(
                config.KASSA2328_API_BASE_URL + path,
                data=body,
                headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise PaymentError(texts.KASSA2328_SERVICE_API_HTTP_ERROR.format(status=response.status))
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        if isinstance(exc, PaymentError):
            raise
        raise PaymentError(texts.KASSA2328_SERVICE_API_REQUEST_FAILED.format(error_type=type(exc).__name__)) from exc
    if not isinstance(data, dict) or data.get("state") != 0 or not isinstance(data.get("result"), dict):
        raise PaymentError(texts.KASSA2328_SERVICE_API_INVALID_RESPONSE)
    return data["result"]


async def payment_info(order_id: str) -> dict:
    """Получает актуальное состояние сохранённого счёта."""
    return await api_request("/v1/payment/info", {"order_id": order_id})
