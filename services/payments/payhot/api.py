import hashlib
import hmac
import json
import re
import time

from decimal import Decimal, InvalidOperation

import aiohttp

from core.settings import payhot_config as config
from settings import texts


class PaymentError(ValueError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def normalize_phone(value) -> str:
    """Приводит номер плательщика SberPay к формату +7XXXXXXXXXX."""
    compact = re.sub(r"[\s()\-]", "", str(value or ""))
    if not re.fullmatch(r"(?:\+7|7|8)[0-9]{10}", compact):
        raise PaymentError(texts.PAYHOT_PHONE_INVALID)
    return "+7" + compact[-10:]


def rub_amount(value) -> Decimal:
    """Проверяет рублёвую сумму без округления копеек."""
    try:
        if isinstance(value, bool):
            raise ValueError
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PaymentError(texts.PAYHOT_AMOUNT_INVALID) from exc
    if not config.PAYHOT_MIN_AMOUNT_RUB <= amount <= config.PAYHOT_MAX_AMOUNT_RUB:
        raise PaymentError(
            texts.PAYHOT_AMOUNT_OUT_OF_RANGE.format(
                minimum=config.PAYHOT_MIN_AMOUNT_RUB, maximum=config.PAYHOT_MAX_AMOUNT_RUB
            )
        )
    return amount.quantize(Decimal("0.01"))


def verify_webhook(raw_body: bytes, signature: str, *, now: float | None = None) -> bool:
    """Проверяет подпись исходного тела и время доставки."""
    match = re.fullmatch(r"t=([0-9]{1,12}),v2=([a-f0-9]{64})", signature or "")
    if not match or not config.PAYHOT_WEBHOOK_SECRET:
        return False
    timestamp, received = match.groups()
    if abs((time.time() if now is None else now) - int(timestamp)) > 300:
        return False
    signed = timestamp.encode("ascii") + b"." + raw_body
    for secret in (config.PAYHOT_WEBHOOK_SECRET, config.PAYHOT_PREVIOUS_WEBHOOK_SECRET):
        if secret:
            expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, received):
                return True
    return False


def _unique_object(pairs: list[tuple]) -> dict:
    """Отклоняет повторяющиеся поля JSON."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющееся поле JSON")
        result[key] = value
    return result


def _reject_constant(value: str):
    """Отклоняет нечисловые константы JSON."""
    raise ValueError("Некорректная константа JSON")


def parse_json(raw_body: bytes) -> dict:
    """Читает однозначный JSON-объект без специальных чисел."""
    data = json.loads(raw_body, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    if not isinstance(data, dict):
        raise ValueError("Ожидался объект JSON")
    return data


def _api_error(status: int, data: dict) -> PaymentError:
    """Возвращает безопасное описание ошибки API без реквизитов."""
    error = data.get("error")
    error = error if isinstance(error, dict) else {}
    if error.get("code") == "payment_amount_out_of_range":
        details = error.get("details")
        details = details if isinstance(details, dict) else {}
        try:
            minimum = Decimal(details["minimum_amount_minor"]) / 100
            maximum = Decimal(details["maximum_amount_minor"]) / 100
            if minimum.is_finite() and maximum.is_finite() and 0 <= minimum <= maximum:
                return PaymentError(texts.PAYHOT_METHOD_AMOUNT_OUT_OF_RANGE.format(minimum=minimum, maximum=maximum))
        except (KeyError, InvalidOperation, TypeError, ValueError):
            pass
    return PaymentError(
        texts.PAYHOT_PROVIDER_ERROR,
        retryable=status >= 500 or status == 429,
    )


async def api_request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Выполняет запрос к рабочему API PayHOT."""
    if not config.PAYHOT_API_KEY or config.PAYHOT_API_KEY.startswith("phk_test_"):
        raise PaymentError(texts.PAYHOT_API_KEY_REQUIRED)
    headers = {"Authorization": f"Bearer {config.PAYHOT_API_KEY}", "Accept": "application/json"}
    if idempotency_key is not None:
        if not re.fullmatch(r"[!-~]{16,128}", idempotency_key):
            raise PaymentError(texts.PAYHOT_IDEMPOTENCY_KEY_INVALID)
        headers["Idempotency-Key"] = idempotency_key
    options = {"json": payload} if payload is not None else {}
    timeout = aiohttp.ClientTimeout(total=config.PAYHOT_REQUEST_TIMEOUT, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                config.PAYHOT_API_BASE_URL + path,
                headers=headers,
                allow_redirects=False,
                **options,
            ) as response:
                raw = await response.read()
                if len(raw) > 1048576:
                    raise PaymentError(texts.PAYHOT_RESPONSE_TOO_LARGE)
                try:
                    data = parse_json(raw)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise PaymentError(texts.PAYHOT_RESPONSE_INVALID, retryable=response.status >= 500) from exc
                if response.status not in {200, 201} or "error" in data:
                    raise _api_error(response.status, data)
                if data.get("environment") == "sandbox":
                    raise PaymentError(texts.PAYHOT_SANDBOX_PAYMENT_REJECTED)
                return data
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise PaymentError(texts.PAYHOT_CONNECTION_ERROR, retryable=True) from exc
