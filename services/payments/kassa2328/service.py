import copy
import hashlib
import json
import re

from collections.abc import Awaitable, Callable
from decimal import ROUND_UP, Decimal
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.client_origin import client_origin
from core.settings import kassa2328_config as config
from core.settings.payments_config import PAYMENTS_CONFIG
from database.access.resolution import UserId
from database.cache_purge import flush_purges
from database.kassa2328 import (
    PAYMENT_SYSTEM,
    REFUND_STATUSES,
    SUCCESS_STATUSES,
    find_checkout_order,
    get_checkout_key,
    load_order,
    lock_checkout_owner,
    save_checkout_result,
    save_order,
    save_response,
)
from database.payments import invalidate_payment_cache
from database.temporary_data import get_temporary_data
from logger import logger
from services.errors import ServiceError
from services.payments.checkout_intent import apply_checkout_intent, freeze_checkout_intent, prepare_checkout_completion
from services.payments.currency_rates import get_rub_rate
from services.payments.kassa2328.api import (
    PaymentError,
    api_request,
    payment_info,
    positive_decimal,
    rub_amount,
    validate_config,
)
from services.payments.payment_links import register_payment_creator
from services.payments.pipeline import ParsedPayment, PipelineResult, process_cancelled_payment, process_success_payment
from settings import texts


_checkout_notifier: Callable[..., Awaitable[None]] | None = None


def register_checkout_notifier(notifier: Callable[..., Awaitable[None]]) -> None:
    """Подключает отображение выполненной покупки в боте."""
    global _checkout_notifier
    _checkout_notifier = notifier


def available() -> bool:
    """Проверяет включение кассы и наличие её реквизитов."""
    if not PAYMENTS_CONFIG.get("KASSA2328", False):
        return False
    try:
        validate_config()
    except (ValueError, TypeError):
        return False
    return True


async def _invoice_amount(amount: Decimal) -> Decimal:
    """Пересчитывает рублёвое пополнение в валюту счёта."""
    rate = positive_decimal(await get_rub_rate(config.KASSA2328_INVOICE_CURRENCY, session=None))
    return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_UP)


def _metadata(metadata: dict | None) -> dict:
    """Удаляет переданные извне служебные поля кассы."""
    result = {key: copy.deepcopy(value) for key, value in (metadata or {}).items() if not key.startswith("kassa2328_")}
    result["provider"] = PAYMENT_SYSTEM
    result["origin"] = client_origin()
    return result


def _key_snapshot(key) -> dict:
    """Фиксирует параметры подписки, от которых зависела цена."""
    fields = (
        "client_id",
        "email",
        "server_id",
        "expiry_time",
        "tariff_id",
        "selected_device_limit",
        "selected_traffic_limit",
        "selected_price_rub",
        "current_device_limit",
        "current_traffic_limit",
        "is_frozen",
    )
    return {field: getattr(key, field, None) for field in fields}


async def create_invoice(
    session: AsyncSession,
    user_ref: int,
    amount,
    *,
    order_id: str | None = None,
    success_url: str | None = None,
    metadata: dict | None = None,
    trusted_legacy: bool = False,
) -> tuple[dict, dict]:
    """Создаёт или повторяет сохранённую попытку оплаты."""
    if not available():
        raise PaymentError(texts.KASSA2328_SERVICE_UNAVAILABLE)
    value = rub_amount(amount)
    if order_id is not None and not re.fullmatch(r"k2328_[0-9a-f]{32}", order_id):
        raise PaymentError(texts.KASSA2328_SERVICE_INVALID_ORDER_ID)
    owner = await lock_checkout_owner(session, user_ref)
    meta = _metadata(metadata)
    temporary = None
    if trusted_legacy:
        if isinstance(meta.get("checkout_data"), dict) and isinstance(meta.get("checkout_state"), str):
            temporary = {"state": meta["checkout_state"], "data": copy.deepcopy(meta["checkout_data"])}
        else:
            temporary = await get_temporary_data(session, owner)
    return_url = success_url or config.KASSA2328_RETURN_URL
    fingerprint_data = {
        "amount": str(value),
        "metadata": meta,
        "temporary": temporary,
        "project": config.KASSA2328_PROJECT_UUID,
        "currency": config.KASSA2328_INVOICE_CURRENCY,
        "callback": config.KASSA2328_CALLBACK_URL,
        "return": return_url,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()
    order = await find_checkout_order(session, owner, fingerprint, order_id=order_id)
    if order is not None:
        if order["amount_rub"] != value:
            raise PaymentError(texts.KASSA2328_SERVICE_SAVED_AMOUNT_CHANGED)
        if order["credited"] or order["provider_status"] in {"cancel", "underpaid", *REFUND_STATUSES}:
            raise PaymentError(texts.KASSA2328_SERVICE_ORDER_FINISHED)
    else:
        checkout_meta = dict(meta)
        if trusted_legacy and temporary:
            checkout_meta["checkout_intent"] = {
                "data": copy.deepcopy(temporary.get("data") or {}),
            }
            checkout_meta.setdefault(
                "payment_flow",
                {
                    "waiting_for_payment": (temporary.get("data") or {}).get("payment_flow", "tariff_purchase"),
                    "waiting_for_renewal_payment": "key_renewal",
                    "waiting_for_addons_payment": "key_addons",
                    "waiting_for_gift_payment": "gift_create",
                }.get(temporary.get("state"), "balance_topup"),
            )
        requested = {**(checkout_meta.get("checkout_intent") or {}).get("data", {}), **checkout_meta}
        key = None
        if str(requested.get("payment_flow") or "").strip().lower() in {"key_renewal", "key_addons"}:
            key = await get_checkout_key(
                session, owner, client_id=requested.get("client_id"), email=requested.get("email")
            )
            if key is None or key.is_frozen:
                raise PaymentError(texts.KASSA2328_SERVICE_SUBSCRIPTION_UNAVAILABLE)
        checkout = await freeze_checkout_intent(
            session, owner, checkout_meta, float(value), trusted_legacy=trusted_legacy
        )
        if key is not None:
            checkout["key_snapshot"] = _key_snapshot(key)
        if temporary:
            checkout["temporary_checkout"] = copy.deepcopy(temporary)
        payload = {
            "amount": format(await _invoice_amount(value), ".2f"),
            "currency": config.KASSA2328_INVOICE_CURRENCY,
            "order_id": order_id or "k2328_" + uuid4().hex,
            "url_callback": config.KASSA2328_CALLBACK_URL,
            "ttl_seconds": config.KASSA2328_TTL_SECONDS,
            "description": texts.KASSA2328_SERVICE_PAYMENT_DESCRIPTION,
        }
        if return_url:
            parsed_url = urlsplit(return_url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                raise PaymentError(texts.KASSA2328_SERVICE_INVALID_RETURN_URL)
            payload["url_return"] = return_url
        meta.update({
            "kassa2328_project": config.KASSA2328_PROJECT_UUID,
            "kassa2328_request": payload,
            "kassa2328_fingerprint": fingerprint,
            "kassa2328_checkout": checkout,
        })
        order = await save_order(session, owner, value, payload, meta)
    await session.commit()
    await flush_purges(session)
    response = await api_request("/v1/payment", json.loads(order["request_json"]))
    order = await save_response(order["order_id"], response)
    link = urlsplit(str(response.get("url") or ""))
    if link.scheme != "https" or not link.hostname:
        raise PaymentError(texts.KASSA2328_SERVICE_INVALID_PAYMENT_URL)
    if response["payment_status"] in SUCCESS_STATUSES:
        result = await settle_payment(response)
        if not result.ok:
            raise PaymentError(texts.KASSA2328_SERVICE_PROCESSING_INCOMPLETE)
    return order, response


async def create_link(
    session: AsyncSession,
    user_ref: int,
    amount: float,
    currency: str,
    success_url: str | None,
    failure_url: str | None,
    metadata: dict | None,
) -> tuple[str, str]:
    """Создаёт ссылку оплаты для аккаунта сайта."""
    if str(currency or "RUB").upper() != "RUB":
        raise PaymentError(texts.KASSA2328_SERVICE_RUB_REQUIRED)
    meta = _metadata(metadata)
    meta.pop("checkout_state", None)
    meta.pop("checkout_data", None)
    meta.pop("checkout_intent", None)
    meta["source"] = "web"
    meta.setdefault("payment_flow", "balance_topup")
    order, response = await create_invoice(session, user_ref, amount, success_url=success_url, metadata=meta)
    return response["url"], order["order_id"]


async def _complete_checkout(session: AsyncSession, owner: UserId, amount: float, row) -> None:
    """Выполняет сохранённую покупку один раз у актуального владельца."""
    metadata = row.metadata_ or {}
    if metadata.get("kassa2328_checkout_result"):
        return
    if metadata.get("kassa2328_status") in REFUND_STATUSES:
        raise PaymentError(texts.KASSA2328_SERVICE_REFUND_IN_PROGRESS)
    intent = metadata.get("kassa2328_checkout")
    if not isinstance(intent, dict):
        return
    intent = copy.deepcopy(intent)
    details: dict = {}
    try:
        async with session.begin_nested():
            intent = await prepare_checkout_completion(session, owner, intent)
            client_id = await apply_checkout_intent(session, owner, intent, amount, result_sink=details)
    except ServiceError as exc:
        await save_checkout_result(session, row, {"status": "review", "reason": str(exc)})
        logger.warning("[2328] Оплата {} зачислена, операция требует проверки: {}", row.payment_id, exc)
        return
    result = {"status": "applied", "client_id": client_id, **details}
    await save_checkout_result(session, row, result)
    if _checkout_notifier is not None:
        try:
            await _checkout_notifier(session, owner, intent, result)
        except Exception as exc:
            logger.warning("[2328] Не удалось отправить результат покупки {}: {}", row.payment_id, type(exc).__name__)


async def settle_payment(data: dict) -> PipelineResult:
    """Обрабатывает проверенный ответ через платёжное ядро."""
    order_id = data.get("order_id")
    if not isinstance(order_id, str) or not re.fullmatch(r"k2328_[0-9a-f]{32}", order_id):
        raise PaymentError(texts.KASSA2328_SERVICE_ORDER_NOT_FOUND)
    order = await save_response(order_id, data)
    status = data["payment_status"]
    effective = order["provider_status"]
    parsed = ParsedPayment(
        payment_id=order_id,
        tg_id=UserId(order["user_id"]) if order["user_id"] is not None else None,
        amount=float(order["amount_rub"]),
        currency="RUB",
        metadata=order["metadata"],
    )
    if status in SUCCESS_STATUSES and effective in SUCCESS_STATUSES:
        immutable = isinstance(order["metadata"].get("kassa2328_checkout"), dict)
        result = await process_success_payment(
            PAYMENT_SYSTEM,
            parsed,
            metadata_patch={"kassa2328_event": {key: value for key, value in data.items() if key != "sign"}},
            update_currency=data["currency"] if data["currency"] != "RUB" else None,
            update_original_amount=float(positive_decimal(data["amount"])) if data["currency"] != "RUB" else None,
            completion=_complete_checkout,
            use_temporary_checkout=not immutable,
        )
    elif status == "cancel" and effective == "cancel":
        result = await process_cancelled_payment(PAYMENT_SYSTEM, parsed)
    elif status == "underpaid" and effective == "underpaid":
        result = await process_cancelled_payment(PAYMENT_SYSTEM, parsed, new_status="failed")
    elif status == "refund_paid" and effective == "refund_paid" and not order["credited"]:
        result = await process_cancelled_payment(PAYMENT_SYSTEM, parsed)
    else:
        result = PipelineResult(ok=True, already_processed=order["credited"])
    if not result.ok:
        raise PaymentError(texts.KASSA2328_SERVICE_PROCESSING_FAILED)
    await invalidate_payment_cache(order_id)
    return result


async def reconcile_payment(payment_id: str) -> str:
    """Сверяет свой сохранённый счёт непосредственно с кассой."""
    order = await load_order(payment_id)
    if order is None:
        raise PaymentError(texts.KASSA2328_SERVICE_ORDER_NOT_FOUND)
    data = await payment_info(payment_id)
    await settle_payment(data)
    current = await load_order(payment_id)
    if current and current["credited"]:
        return "paid"
    return (current or {}).get("provider_status") or data["payment_status"]


register_payment_creator("KASSA2328", create_link)
