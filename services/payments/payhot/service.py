import copy
import re

from decimal import Decimal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from core.settings import payhot_config as config
from core.settings.payments_config import PAYMENTS_CONFIG
from database import async_session_maker, register_pending_payment
from database.payments import (
    PAYMENT_CHECKOUT_SNAPSHOT,
    _checkout_snapshot,
    canonical_payment_provider,
    get_payment_from_db_by_payment_id,
    invalidate_payment_cache,
    lock_payment_for_processing,
    require_payment_provider,
    resolve_payment_creation_owner,
)
from logger import logger
from services.admin_alert import send_admin_alert
from services.payments.checkout_snapshot import capture_payment_checkout
from services.payments.payhot.api import PaymentError, api_request, normalize_phone, rub_amount
from services.payments.payment_links import register_payment_creator
from services.payments.pipeline import ParsedPayment, PipelineResult, process_cancelled_payment, process_success_payment
from settings import texts


PAYHOT_DIFFERENT_PAYMENT = "PayHOT вернул другой платёж."
PAYHOT_WEBHOOK_INVALID = "Некорректное уведомление PayHOT."


PAYHOT_METHODS = {
    "cards": {"provider_id": "PAYHOT_CARDS", "payment_method": "card"},
    "sbp": {"provider_id": "PAYHOT_SBP", "payment_method": "sbp"},
    "sberpay": {"provider_id": "PAYHOT_SBERPAY", "payment_method": "sberpay"},
}
_FINAL_STATUSES = {"succeeded", "failed", "cancelled", "refunded", "partially_refunded"}


def available(method_name: str) -> bool:
    """Проверяет включение способа и наличие рабочих реквизитов."""
    method = PAYHOT_METHODS.get(method_name)
    return bool(
        method
        and PAYMENTS_CONFIG.get(method["provider_id"], False)
        and config.PAYHOT_API_KEY
        and not config.PAYHOT_API_KEY.startswith("phk_test_")
        and config.PAYHOT_WEBHOOK_SECRET
    )


def _order_id(value: str) -> str:
    """Проверяет собственный номер счёта PayHOT."""
    if not isinstance(value, str) or not re.fullmatch(r"payhot_[0-9a-f]{32}", value):
        raise PaymentError(texts.PAYHOT_INVOICE_ID_INVALID)
    return value


def _provider_id(value) -> str:
    """Проверяет идентификатор платежа перед запросом к API."""
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise PaymentError(texts.PAYHOT_PAYMENT_ID_INVALID) from exc


async def _load_invoice(payment_id: str) -> dict:
    """Читает сохранённый счёт без поиска владельца по уведомлению."""
    async with async_session_maker() as session:
        invoice = await get_payment_from_db_by_payment_id(session, _order_id(payment_id))
    if not invoice or canonical_payment_provider(invoice["payment_system"]) != "payhot":
        raise PaymentError(texts.PAYHOT_INVOICE_NOT_FOUND)
    if invoice.get("user_id") is None or (invoice.get("metadata") or {}).get("owner_deleted") is True:
        raise PaymentError(texts.PAYHOT_INVOICE_OWNER_UNAVAILABLE)
    return invoice


def _validate_payment(payment: dict, invoice: dict) -> str:
    """Сверяет платёж кассы с неизменными условиями счёта."""
    if not isinstance(payment, dict) or payment.get("environment") == "sandbox":
        raise PaymentError(texts.PAYHOT_PAYMENT_INVALID)
    provider_id = _provider_id(payment.get("id"))
    metadata = invoice.get("metadata") or {}
    saved = metadata.get("payhot_request") or {}
    expected_minor = str(int(Decimal(str(invoice["amount"])) * 100))
    if (
        payment.get("merchant_order_reference") != invoice["payment_id"]
        or payment.get("amount_minor") != expected_minor
        or payment.get("currency") != "RUB"
        or invoice.get("currency") != "RUB"
        or payment.get("payment_method") != saved.get("payment_method")
        or saved.get("amount_minor") != expected_minor
        or saved.get("merchant_order_reference") != invoice["payment_id"]
        or saved.get("currency") != "RUB"
        or saved.get("payment_method") not in {"card", "sbp", "sberpay"}
        or metadata.get("payhot_payment_id") not in {None, provider_id}
    ):
        raise PaymentError(texts.PAYHOT_PAYMENT_MISMATCH)
    denomination = payment.get("amount_denomination")
    if denomination is not None and (
        not isinstance(denomination, dict)
        or denomination.get("money_unit_code") != "RUB"
        or denomination.get("unit_kind") != "fiat"
        or denomination.get("exponent") != 2
    ):
        raise PaymentError(texts.PAYHOT_DENOMINATION_INVALID)
    cash_desk = payment.get("cash_desk")
    if (
        cash_desk is not None
        and metadata.get("payhot_cash_desk_id")
        and (not isinstance(cash_desk, dict) or cash_desk.get("id") != metadata["payhot_cash_desk_id"])
    ):
        raise PaymentError(texts.PAYHOT_CASH_DESK_MISMATCH)
    if payment.get("status") == "succeeded" and payment.get("captured_amount_minor", expected_minor) != expected_minor:
        raise PaymentError(texts.PAYHOT_PAYMENT_CAPTURE_INCOMPLETE)
    if not isinstance(payment.get("status"), str) or not payment["status"]:
        raise PaymentError(texts.PAYHOT_PAYMENT_STATUS_MISSING)
    return provider_id


async def _save_provider_state(payment: dict, *, event_id: str | None = None) -> dict:
    """Привязывает ответ кассы к счёту под блокировкой владельца."""
    payment_id = _order_id(payment.get("merchant_order_reference"))
    async with async_session_maker() as session:
        row = await lock_payment_for_processing(session, payment_id)
        if row is None or row.user_id is None or (row.metadata_ or {}).get("owner_deleted") is True:
            raise PaymentError(texts.PAYHOT_INVOICE_PROCESSING_UNAVAILABLE)
        require_payment_provider(row, "payhot")
        invoice = {
            "payment_id": row.payment_id,
            "user_id": row.user_id,
            "amount": row.amount,
            "currency": row.currency,
            "metadata": row.metadata_,
        }
        provider_id = _validate_payment(payment, invoice)
        metadata = dict(row.metadata_ or {})
        metadata.update(payhot_payment_id=provider_id, payhot_status=payment["status"])
        if isinstance(payment.get("cash_desk"), dict) and payment["cash_desk"].get("id"):
            metadata["payhot_cash_desk_id"] = payment["cash_desk"]["id"]
        if event_id:
            metadata["payhot_last_event_id"] = event_id
        row.metadata_ = metadata
        await session.commit()
        invoice["metadata"] = metadata
    await invalidate_payment_cache(payment_id)
    return invoice


async def _process_confirmed(payment: dict, *, event_id: str | None = None) -> PipelineResult:
    """Передаёт подтверждённый кассой статус в общий платёжный процесс."""
    invoice = await _save_provider_state(payment, event_id=event_id)
    parsed = ParsedPayment(
        payment_id=invoice["payment_id"],
        tg_id=None,
        amount=float(invoice["amount"]),
        currency="RUB",
    )
    status = payment["status"]
    if status == "succeeded":
        result = await process_success_payment("payhot", parsed)
    elif status in {"failed", "cancelled", "refunded"}:
        result = await process_cancelled_payment(
            "payhot", parsed, new_status={"failed": "failed", "cancelled": "cancelled", "refunded": "refunded"}[status]
        )
    else:
        if status == "partially_refunded":
            logger.warning(
                "[PayHOT] Частичный возврат счёта {} требует проверки администратором", invoice["payment_id"]
            )
            await _alert_partial_refund(invoice)
        result = PipelineResult(ok=True)
    if not result.ok:
        raise PaymentError(texts.PAYHOT_PROCESSING_FAILED, retryable=True)
    return result


async def _alert_partial_refund(invoice: dict) -> None:
    """Уведомляет о ручной сверке частичного возврата."""
    if invoice["metadata"].get("payhot_manual_review_notified") is True:
        return
    delivered = await send_admin_alert(
        "⚠️ PayHOT: частичный возврат требует ручной сверки.\n"
        f"Счёт: {invoice['payment_id']}\n"
        f"Пользователь: {invoice['user_id']}\n"
        f"Исходная сумма: {invoice['amount']} RUB\n"
        "Проверьте оплату и сумму возврата в кабинете PayHOT и скорректируйте баланс клиента."
    )
    if not delivered:
        raise PaymentError(texts.PAYHOT_PARTIAL_REFUND_ALERT_FAILED, retryable=True)
    async with async_session_maker() as session:
        row = await lock_payment_for_processing(session, invoice["payment_id"])
        if row is not None:
            require_payment_provider(row, "payhot")
            row.metadata_ = {**(row.metadata_ or {}), "payhot_manual_review_notified": True}
            await session.commit()
    await invalidate_payment_cache(invoice["payment_id"])


async def create_invoice(
    session: AsyncSession,
    user_ref: int,
    amount,
    method_name: str,
    *,
    metadata: dict | None = None,
    order_id: str | None = None,
    expected_checkout: dict | None = None,
) -> tuple[str, str]:
    """Сохраняет счёт до запроса и получает полную ссылку оплаты."""
    if not available(method_name):
        raise PaymentError(texts.PAYHOT_METHOD_UNAVAILABLE)
    value = rub_amount(amount)
    customer_phone = (
        normalize_phone((metadata or {}).get("payhot_customer_phone")) if method_name == "sberpay" else None
    )
    owner = await resolve_payment_creation_owner(session, user_ref)
    payment_id = _order_id(order_id) if order_id is not None else "payhot_" + uuid4().hex
    method = PAYHOT_METHODS[method_name]
    payload = {
        "merchant_order_reference": payment_id,
        "amount_minor": str(int(value * 100)),
        "currency": "RUB",
        "payment_method": method["payment_method"],
        "customer": {"id": f"u{int(owner)}"},
    }
    if customer_phone is not None:
        payload["customer"].update(phone=customer_phone, sberpay={"front_type": "back2app"})
    previous = await get_payment_from_db_by_payment_id(session, payment_id) if order_id else None
    if previous:
        if (
            canonical_payment_provider(previous["payment_system"]) != "payhot"
            or previous.get("user_id") != owner
            or Decimal(str(previous["amount"])) != value
            or (previous.get("metadata") or {}).get("payhot_request") != payload
            or previous.get("status") in {"success", "refunded", "chargebacked"}
        ):
            raise PaymentError(texts.PAYHOT_INVOICE_CONFLICT)
    else:
        meta = {key: copy.deepcopy(item) for key, item in (metadata or {}).items() if not key.startswith("payhot_")}
        meta = await capture_payment_checkout(session, owner, meta)
        if expected_checkout is not None and meta.get(PAYMENT_CHECKOUT_SNAPSHOT) != _checkout_snapshot(
            expected_checkout
        ):
            raise PaymentError(texts.PAYHOT_PURCHASE_CHANGED)
        meta.update(provider="payhot", payhot_method=method_name, payhot_request=payload)
        await session.commit()
        await register_pending_payment(payment_id, owner, float(value), "payhot", currency="RUB", metadata=meta)
    await session.commit()
    for attempt in range(2):
        try:
            response = await api_request("POST", "/payments", payload=payload, idempotency_key=payment_id)
            break
        except PaymentError as exc:
            if attempt or not exc.retryable:
                raise
    payment = response.get("payment")
    invoice = await _load_invoice(payment_id)
    provider_id = _validate_payment(payment, invoice)
    if payment["status"] in _FINAL_STATUSES:
        await _process_confirmed(payment)
        raise PaymentError(texts.PAYHOT_PAYMENT_COMPLETED)
    await _save_provider_state(payment)
    checkout = response.get("checkout")
    if isinstance(checkout, dict) and not checkout.get("url") and checkout.get("reissue_required") is True:
        response = await api_request("POST", f"/payments/{provider_id}/checkout-sessions")
        _validate_payment(response.get("payment"), await _load_invoice(payment_id))
        checkout = response.get("checkout")
    payment_url = checkout.get("url") if isinstance(checkout, dict) else None
    parsed_url = urlsplit(payment_url) if isinstance(payment_url, str) else None
    if (
        parsed_url is None
        or parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
    ):
        raise PaymentError(texts.PAYHOT_PAYMENT_URL_MISSING)
    return payment_url, payment_id


async def settle_payment(data: dict, *, event_id: str | None = None) -> PipelineResult:
    """Сверяет подписанное уведомление с API и сохранённым счётом."""
    if not isinstance(data, dict):
        raise PaymentError(PAYHOT_WEBHOOK_INVALID)
    invoice = await _load_invoice(data.get("merchant_order_reference"))
    provider_id = _validate_payment(data, invoice)
    response = await api_request("GET", f"/payments/{provider_id}")
    payment = response.get("payment")
    if _validate_payment(payment, invoice) != provider_id:
        raise PaymentError(PAYHOT_DIFFERENT_PAYMENT)
    return await _process_confirmed(payment, event_id=event_id)


async def reconcile_payment(payment_id: str) -> str:
    """Восстанавливает оплату через API при пропущенном уведомлении."""
    invoice = await _load_invoice(payment_id)
    metadata = invoice.get("metadata") or {}
    provider_id = metadata.get("payhot_payment_id")
    if provider_id:
        response = await api_request("GET", f"/payments/{_provider_id(provider_id)}")
    else:
        response = await api_request(
            "POST", "/payments", payload=metadata["payhot_request"], idempotency_key=payment_id
        )
    payment = response.get("payment")
    _validate_payment(payment, invoice)
    await _process_confirmed(payment)
    async with async_session_maker() as session:
        current = await get_payment_from_db_by_payment_id(session, payment_id)
    return str((current or {}).get("status") or "")


def _creator(method_name: str):
    """Создаёт адаптер реестра ссылок для способа PayHOT."""

    async def create_link(
        session: AsyncSession,
        user_ref: int,
        amount: float,
        currency: str,
        success_url: str | None,
        failure_url: str | None,
        metadata: dict | None,
    ) -> tuple[str, str]:
        """Передаёт условия веб-оплаты в создание счёта."""
        if currency.upper() != "RUB":
            raise PaymentError(texts.PAYHOT_CURRENCY_UNSUPPORTED)
        return await create_invoice(
            session,
            user_ref,
            amount,
            method_name,
            metadata=metadata,
            expected_checkout=(metadata or {}).get(PAYMENT_CHECKOUT_SNAPSHOT),
        )

    return create_link


for _method_name, _method in PAYHOT_METHODS.items():
    register_payment_creator(_method["provider_id"], _creator(_method_name))
