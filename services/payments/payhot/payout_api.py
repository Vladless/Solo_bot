import copy
import re
import time

from datetime import datetime
from uuid import UUID

from core.settings import payhot_config as config
from services.payments.payhot.api import PaymentError, api_request
from settings import texts


_REFERENCE_PATTERN = r"[A-Za-z][A-Za-z0-9]{0,31}[-_:][A-Za-z0-9][A-Za-z0-9._:-]{0,94}"
_NAME_PATTERN = r"[A-Za-zА-Яа-яЁё '’\-]+"
_BANK_PATTERN = r"100[0-9]{9}"
_STATUSES = {"processing", "succeeded", "failed", "cancelled"}
_banks_cache = None


class PayoutError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.unknown = unknown


def _recipient_error() -> PayoutError:
    """Возвращает безопасную ошибку реквизитов."""
    return PayoutError(texts.PARTNER_PAYHOT_RECIPIENT_INVALID, code="recipient_invalid")


def _provider_error(code: str, *, unknown: bool = False, retryable: bool = False) -> PayoutError:
    """Скрывает технические данные ошибки выплаты."""
    return PayoutError(texts.PARTNER_PAYHOT_PROVIDER_ERROR, code=code, unknown=unknown, retryable=retryable)


def _valid_name(value) -> bool:
    """Проверяет отдельную часть имени получателя."""
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value == value.strip(" ")
        and re.fullmatch(_NAME_PATTERN, value)
        and re.search(r"[A-Za-zА-Яа-яЁё]", value)
    )


def _valid_email(value) -> bool:
    """Проверяет адрес без отображаемого имени и пробелов."""
    if not isinstance(value, str) or not 3 <= len(value) <= 254 or value.count("@") != 1:
        return False
    local, domain = value.split("@")
    if (
        not 1 <= len(local) <= 64
        or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+", local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
    ):
        return False
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels
    )


def normalize_recipient(method: str, data: dict) -> dict:
    """Проверяет и нормализует реквизиты получателя выплаты."""
    if not isinstance(method, str) or method not in {"card", "sbp"} or not isinstance(data, dict):
        raise _recipient_error()
    first_name, last_name = data.get("first_name"), data.get("last_name")
    if not _valid_name(first_name) or not _valid_name(last_name) or len(first_name + " " + last_name) > 200:
        raise _recipient_error()
    phone = data.get("phone")
    if (
        not isinstance(phone, str)
        or not 11 <= len(phone) <= 40
        or not re.fullmatch(r"\+?7(?:[ ()-]*[0-9]){10}[ ()-]*", phone)
    ):
        raise _recipient_error()
    result = {
        "method": method,
        "first_name": first_name,
        "last_name": last_name,
        "phone": re.sub(r"[+ ()-]", "", phone),
    }
    if method == "card":
        card_number = data.get("card_number")
        if not isinstance(card_number, str) or len(card_number) > 64:
            raise _recipient_error()
        card_number = re.sub(r"[ -]", "", card_number)
        if not re.fullmatch(r"[0-9]{16}", card_number) or not _valid_email(data.get("email")):
            raise _recipient_error()
        result.update(card_number=card_number, email=data["email"])
    else:
        bank_id = data.get("bank_id")
        if not isinstance(bank_id, str) or not re.fullmatch(_BANK_PATTERN, bank_id):
            raise _recipient_error()
        result["bank_id"] = bank_id
    return result


def _valid_amount(value, *, allow_zero: bool = False, maximum: int = 9223372036854775807) -> bool:
    """Проверяет каноническую сумму в минимальных единицах."""
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= len(str(maximum))
        and re.fullmatch(r"0|[1-9][0-9]*" if allow_zero else r"[1-9][0-9]*", value)
        and int(value) <= maximum
    )


def _valid_reference(value) -> bool:
    """Проверяет собственный номер выплаты."""
    return isinstance(value, str) and len(value) <= 128 and bool(re.fullmatch(_REFERENCE_PATTERN, value))


def _payout_id(value) -> str:
    """Проверяет UUID перед обращением к API."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", value
    ):
        raise _provider_error("payout_id_invalid")
    return str(UUID(value))


def _valid_datetime(value) -> bool:
    """Проверяет дату со временем и часовым поясом."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})",
        value,
    ):
        return False
    try:
        return datetime.fromisoformat(value.replace("z", "Z")).utcoffset() is not None
    except ValueError:
        return False


def _request_fields(payload: dict) -> tuple[str, str, str]:
    """Проверяет неизменные условия исходной выплаты."""
    if not isinstance(payload, dict):
        raise _provider_error("payout_request_invalid")
    recipient = payload.get("recipient")
    if (
        not _valid_reference(payload.get("merchant_payout_reference"))
        or not _valid_amount(payload.get("amount_minor"))
        or payload.get("currency") != "RUB"
        or not isinstance(recipient, dict)
        or not isinstance(recipient.get("method"), str)
        or recipient.get("method") not in {"card", "sbp"}
    ):
        raise _provider_error("payout_request_invalid")
    return payload["merchant_payout_reference"], payload["amount_minor"], recipient["method"]


def _payout_object(value, *, payout_id: str | None = None) -> dict:
    """Проверяет опубликованную схему состояния выплаты."""
    if not isinstance(value, dict):
        raise _provider_error("payout_response_invalid")
    provider_id = _payout_id(value.get("id"))
    if (
        value["id"] != provider_id
        or value.get("environment") == "sandbox"
        or not _valid_reference(value.get("merchant_payout_reference"))
        or not _valid_amount(value.get("amount_minor"))
        or value.get("currency") != "RUB"
        or type(value.get("money_unit_exponent")) is not int
        or value["money_unit_exponent"] != 2
        or not isinstance(value.get("payment_method"), str)
        or value.get("payment_method") not in {"card", "sbp"}
        or not isinstance(value.get("status"), str)
        or value.get("status") not in _STATUSES
        or not _valid_datetime(value.get("created_at"))
        or not _valid_datetime(value.get("updated_at"))
        or ("gross_amount_minor" in value and not _valid_amount(value["gross_amount_minor"]))
        or (
            "fee_amount_minor" in value
            and not _valid_amount(value["fee_amount_minor"], allow_zero=True, maximum=10**38 - 1)
        )
        or (
            "instrument_display_mask" in value
            and (
                not isinstance(value["instrument_display_mask"], str)
                or not 1 <= len(value["instrument_display_mask"]) <= 64
            )
        )
    ):
        raise _provider_error("payout_response_invalid")
    if payout_id is not None and provider_id != _payout_id(payout_id):
        raise _provider_error("payout_mismatch")
    return copy.deepcopy(value)


def validate_payout(payout: dict, payload: dict, payout_id: str | None = None) -> dict:
    """Сверяет выплату с сохранёнными условиями отправки."""
    result = _payout_object(payout, payout_id=payout_id)
    reference, amount, method = _request_fields(payload)
    if (
        result["merchant_payout_reference"] != reference
        or result["amount_minor"] != amount
        or result["payment_method"] != method
    ):
        raise _provider_error("payout_mismatch")
    return result


async def list_sbp_banks() -> list[dict]:
    """Получает справочник банков с кешем до пяти минут."""
    global _banks_cache
    scope = (config.PAYHOT_API_BASE_URL, config.PAYHOT_API_KEY)
    now = time.monotonic()
    if _banks_cache is not None:
        cached_scope, expires_at, items = _banks_cache
        if cached_scope == scope and now < expires_at:
            return copy.deepcopy(items)
    try:
        response = await api_request("GET", "/payouts/sbp-banks")
        items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(items, list):
            raise ValueError
        result, seen = [], set()
        for item in items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("bank_id"), str)
                or not re.fullmatch(_BANK_PATTERN, item["bank_id"])
                or item["bank_id"] in seen
                or not isinstance(item.get("name"), str)
                or not item["name"].strip()
                or any(ord(char) < 32 for char in item["name"])
            ):
                raise ValueError
            seen.add(item["bank_id"])
            result.append({"bank_id": item["bank_id"], "name": item["name"]})
    except (PaymentError, ValueError, TypeError, KeyError) as exc:
        raise PayoutError(
            texts.PARTNER_PAYHOT_BANKS_UNAVAILABLE,
            code="banks_unavailable",
            retryable=True,
        ) from exc
    _banks_cache = (scope, now + 300, copy.deepcopy(result))
    return result


async def create_payout(payload: dict, idempotency_key: str) -> dict:
    """Отправляет сохранённую заявку с прежним ключом повтора."""
    _request_fields(payload)
    if not isinstance(idempotency_key, str) or not re.fullmatch(r"[!-~]{16,128}", idempotency_key):
        raise _provider_error("payout_request_invalid")
    recipient = payload["recipient"]
    normalize_recipient(recipient["method"], recipient)
    identifier = recipient.get("payeer_identifier")
    if (
        not isinstance(identifier, str)
        or not 1 <= len(identifier) <= 128
        or not identifier.strip()
        or any(char in identifier for char in "\r\n\0")
        or (recipient["method"] == "card" and not re.fullmatch(r"[0-9]{16}", recipient["card_number"]))
    ):
        raise _recipient_error()
    saved_payload = copy.deepcopy(payload)
    try:
        response = await api_request("POST", "/payouts", payload=saved_payload, idempotency_key=idempotency_key)
        if not isinstance(response, dict) or response.get("environment") == "sandbox":
            raise _provider_error("payout_response_invalid")
        return validate_payout(response.get("payout"), saved_payload)
    except (TypeError, ValueError) as exc:
        raise _provider_error(
            exc.code if isinstance(exc, PayoutError) else "provider_error",
            unknown=True,
            retryable=bool(getattr(exc, "retryable", False)),
        ) from exc


async def get_payout(payout_id: str) -> dict:
    """Читает и проверяет выплату по UUID PayHOT."""
    provider_id = _payout_id(payout_id)
    try:
        response = await api_request("GET", f"/payouts/{provider_id}")
        if not isinstance(response, dict) or response.get("environment") == "sandbox":
            raise _provider_error("payout_response_invalid")
        return _payout_object(response.get("payout"), payout_id=provider_id)
    except PaymentError as exc:
        raise _provider_error("provider_error", retryable=exc.retryable) from exc
