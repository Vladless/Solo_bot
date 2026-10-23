from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from ipaddress import ip_address
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.overpay_autopay_config import (
    OVERPAY_AUTOPAY_CONFIG,
    is_overpay_autopay_enabled,
    is_overpay_autopay_enabled_for_session,
)
from database import (
    async_session_maker,
    overpay_autopay as dal,
)
from database.access.resolution import UserId, resolve_user_optional
from database.cache_purge import flush_purges
from database.models import OverPayAutopayLog
from database.payments import add_payment, lock_payment_for_processing, update_payment_status
from database.tariffs import get_tariff_by_id
from database.users import update_balance
from database.web_notifications import notify_web
from logger import logger
from services.admin_alert import send_admin_alert
from services.errors import ServiceError
from services.keys import execute_renewal, normalize_expiry_ms
from services.payments.checkout_intent import (
    apply_checkout_intent,
    autopay_terms_for_tariff,
    checkout_flow,
    checkout_key_snapshot,
    freeze_checkout_intent,
    prepare_checkout_completion,
)
from services.payments.pipeline import (
    CheckoutCompletion,
    ParsedPayment,
    PipelineResult,
    process_cancelled_payment,
    process_success_payment,
)
from settings import texts
from settings.config import (
    OVERPAY_API_URL,
    OVERPAY_CARDS_TERMINAL_ID,
    OVERPAY_CERT_PATH,
    OVERPAY_LIVETIME_MINUTES,
    OVERPAY_PASSWORD,
    OVERPAY_RETURN_URL,
    OVERPAY_SERVER_IP,
    OVERPAY_USERNAME,
    REDIRECT_LINK,
)

from .client import client, money


PROVIDER = "OVERPAY_AUTOPAY"


def require_payment_configuration() -> None:
    """Проверяет реквизиты до создания платёжного заказа."""
    fields = {
        "OVERPAY_API_URL": OVERPAY_API_URL,
        "OVERPAY_USERNAME": OVERPAY_USERNAME,
        "OVERPAY_PASSWORD": OVERPAY_PASSWORD,
        "OVERPAY_CERT_PATH": OVERPAY_CERT_PATH,
        "OVERPAY_CARDS_TERMINAL_ID": OVERPAY_CARDS_TERMINAL_ID,
        "OVERPAY_SERVER_IP": OVERPAY_SERVER_IP,
    }
    missing = [name for name, value in fields.items() if not str(value or "").strip()]
    if missing:
        logger.error("OverPay: не заполнены настройки: {}", ", ".join(missing))
        raise ValueError(texts.OVERPAY_AUTOPAY_UNAVAILABLE)
    try:
        ip_address(str(OVERPAY_SERVER_IP).strip())
    except ValueError as exc:
        raise ValueError(texts.OVERPAY_AUTOPAY_UNAVAILABLE) from exc


async def _commit(session: AsyncSession) -> None:
    await session.commit()
    await flush_purges(session)


def get_config() -> dict:
    return {**OVERPAY_AUTOPAY_CONFIG, "ENABLED": is_overpay_autopay_enabled()}


def _return_url(value: str | None) -> str:
    result = str(value or REDIRECT_LINK or "")
    parsed = urlparse(result)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError(texts.OVERPAY_AUTOPAY_RETURN_URL_REQUIRED)
    return result


def _payload(user_id: int, amount: Decimal, payment_id: str, success_url=None, failure_url=None) -> dict:
    return {
        "amount": str(amount),
        "currency": "RUB",
        "livetimeMinutes": max(5, min(144000, int(OVERPAY_LIVETIME_MINUTES or 60))),
        "projectId": str(OVERPAY_CARDS_TERMINAL_ID),
        "paymentMethods": ["card"],
        "merchantTransactionId": payment_id,
        "options": {"recurring": True},
        "returnUrl": _return_url(success_url or OVERPAY_RETURN_URL),
    }


def _set_status(session: AsyncSession, attempt, status: str) -> None:
    if attempt.status != status:
        attempt.status = status
        session.add(OverPayAutopayLog(attempt_id=attempt.id, status=status, reason=attempt.review_reason))


async def quote_autopay_for_key(session: AsyncSession, user_id: UserId, client_id: str) -> dict:
    key = await dal.get_owned_key(session, UserId(user_id), client_id)
    if key is None or key.is_frozen:
        raise ValueError(texts.OVERPAY_AUTOPAY_SUBSCRIPTION_UNAVAILABLE)
    tariff = await get_tariff_by_id(session, key.tariff_id)
    if not tariff:
        raise ValueError(texts.OVERPAY_AUTOPAY_TARIFF_NOT_FOUND)
    terms = autopay_terms_for_tariff(
        tariff, key.selected_device_limit, key.selected_traffic_limit, provider_id=PROVIDER
    )
    if not terms or int(terms.get("period_days") or tariff.get("duration_days") or 0) <= 0:
        raise ValueError(texts.OVERPAY_AUTOPAY_TARIFF_UNAVAILABLE)
    amount = money(terms["amount"])
    minimum = max(Decimal("10"), money(OVERPAY_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 10)))
    if amount < minimum:
        raise ValueError(texts.OVERPAY_AUTOPAY_MINIMUM_RECURRING_AMOUNT.format(minimum=minimum))
    return {
        "amount": float(amount),
        "period_days": int(terms.get("period_days") or tariff["duration_days"]),
        "label": tariff.get("name") or texts.OVERPAY_AUTOPAY_SUBSCRIPTION_LABEL,
        "tariff_id": int(key.tariff_id),
        "client_id": key.client_id,
        "key_email": key.email,
        "expiry_ms": normalize_expiry_ms(key.expiry_time),
        "selected_device_limit": key.selected_device_limit,
        "selected_traffic_limit": key.selected_traffic_limit,
        "server_id": key.server_id,
    }


def _next_payment_date(expiry_ms: int, period_days: int) -> datetime:
    """Ограничивает раннее списание длительностью оплачиваемого периода."""
    lead_days = min(int(OVERPAY_AUTOPAY_CONFIG.get("DAYS_BEFORE_EXPIRY", 3)), max(0, period_days - 1))
    return datetime.utcfromtimestamp(expiry_ms / 1000) - timedelta(days=lead_days)


async def enable_autopay_for_key(
    session: AsyncSession,
    user_id: UserId,
    client_id: str,
    method_id: str | None = None,
    *,
    consent: dict | None = None,
):
    if not await is_overpay_autopay_enabled_for_session(session):
        raise ValueError(texts.OVERPAY_AUTOPAY_NEW_PAYMENTS_DISABLED)
    require_payment_configuration()
    if not isinstance(consent, dict) or consent.get("accepted") is not True:
        raise ValueError(texts.OVERPAY_AUTOPAY_RECURRING_CONSENT_REQUIRED)
    if consent.get("amount") is None or consent.get("period_days") is None:
        raise ValueError(texts.OVERPAY_AUTOPAY_TERMS_CONSENT_REQUIRED)
    await dal.get_owner(session, user_id, lock=True)
    quote = await quote_autopay_for_key(session, user_id, client_id)
    if money(quote["amount"]) > money(consent["amount"]):
        raise ValueError(texts.OVERPAY_AUTOPAY_AMOUNT_CHANGED)
    if quote["period_days"] != int(consent["period_days"]):
        raise ValueError(texts.OVERPAY_AUTOPAY_PERIOD_CHANGED)
    method = (
        await dal.get_owned_method(session, user_id, method_id)
        if method_id
        else await dal.get_active_method(session, user_id)
    )
    if method is None:
        raise ValueError(texts.OVERPAY_AUTOPAY_METHOD_REQUIRED)
    return await dal.upsert_subscription(
        session,
        user_id,
        client_id,
        method.id,
        amount=money(quote["amount"]),
        period_days=quote["period_days"],
        tariff_id=quote["tariff_id"],
        next_payment_date=_next_payment_date(quote["expiry_ms"], quote["period_days"]),
        consent={**consent, "amount": str(money(quote["amount"])), "period_days": quote["period_days"]},
    )


async def create_checkout_payment(
    session: AsyncSession,
    user_id: UserId,
    amount: float,
    *,
    metadata: dict | None = None,
    success_url: str | None = None,
    failure_url: str | None = None,
) -> tuple[str, str]:
    raw = session if isinstance(session, AsyncSession) else getattr(session, "_session", None)
    if raw is None:
        async with async_session_maker() as owned:
            return await _create_checkout_payment(
                owned, user_id, amount, metadata=metadata, success_url=success_url, failure_url=failure_url
            )
    return await _create_checkout_payment(
        raw, user_id, amount, metadata=metadata, success_url=success_url, failure_url=failure_url
    )


async def _create_checkout_payment(
    session: AsyncSession,
    user_id: UserId,
    amount: float,
    *,
    metadata: dict | None = None,
    success_url: str | None = None,
    failure_url: str | None = None,
) -> tuple[str, str]:
    if not isinstance(user_id, UserId):
        raise ValueError(texts.OVERPAY_AUTOPAY_OWNER_ID_REQUIRED)
    if not await is_overpay_autopay_enabled_for_session(session):
        raise ValueError(texts.OVERPAY_AUTOPAY_NEW_PAYMENTS_DISABLED)
    require_payment_configuration()
    await dal.get_owner(session, user_id, lock=True)
    value = money(amount)
    minimum = max(Decimal("10"), money(OVERPAY_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 10)))
    if value < minimum:
        raise ValueError(texts.OVERPAY_AUTOPAY_MINIMUM_CHECKOUT_AMOUNT.format(minimum=minimum))
    metadata = {**copy.deepcopy(metadata or {}), "autopay_provider": PROVIDER}
    if metadata.get("autopay_consent") is not True:
        raise ValueError(texts.OVERPAY_AUTOPAY_SAVE_CONSENT_REQUIRED)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"amount": str(value), "metadata": metadata}, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()
    attempt = await dal.find_open_checkout_attempt(session, user_id, fingerprint)
    if attempt is None:
        checkout = await freeze_checkout_intent(session, user_id, metadata, float(value))
        attempt_id = str(uuid.uuid4())
        payment_id = f"ovpa_{uuid.UUID(attempt_id).hex}"
        cost = money((checkout.get("data") or {}).get("cost") or 0, positive=False)
        reserved = max(Decimal("0"), cost - value)
        intent = {
            "kind": "checkout",
            "checkout": checkout,
            "metadata": metadata,
            "checkout_fingerprint": fingerprint,
            "reserved_balance": float(reserved),
            "original_user_id": int(user_id),
            "autopay_consent": True,
        }
        attempt = await dal.prepare_attempt(
            session,
            user_id,
            attempt_id=attempt_id,
            payment_id=payment_id,
            payload=_payload(user_id, value, payment_id, success_url, failure_url),
            intent=intent,
        )
        if reserved and await update_balance(session, user_id, -float(reserved)) is None:
            raise ValueError(texts.OVERPAY_AUTOPAY_BALANCE_CHANGED)
        await add_payment(
            session,
            user_id=user_id,
            amount=float(value),
            payment_system=PROVIDER,
            payment_id=payment_id,
            status="pending",
            currency="RUB",
            metadata={"autopay_attempt_id": attempt_id},
        )
    attempt_id = attempt.id
    await _commit(session)
    result = await _submit_attempt(attempt_id)
    async with async_session_maker() as read_session:
        saved = await dal.get_attempt(read_session, attempt_id)
        if saved is not None and saved.confirmation_url and saved.status not in {"canceled", "manual_review"}:
            return saved.confirmation_url, saved.payment_id
        if saved is not None and saved.status == "applied":
            return _return_url(success_url), saved.payment_id
    raise ValueError(result.error or texts.OVERPAY_AUTOPAY_PAYMENT_CHECKING)


async def cancel_subscription(session: AsyncSession, user_id: UserId, subscription_id: int) -> bool:
    result = await dal.cancel_subscription(session, user_id, subscription_id)
    await _commit(session)
    return result


async def create_link(session, user_ref, amount, currency, success_url, failure_url, metadata):
    """Создаёт ссылку оплаты для общего списка провайдеров."""
    if str(currency).upper() != "RUB":
        raise ValueError(texts.OVERPAY_AUTOPAY_CURRENCY_UNSUPPORTED)
    user = await resolve_user_optional(session, user_ref)
    if user is None:
        raise ValueError(texts.OVERPAY_AUTOPAY_OWNER_NOT_FOUND)
    return await create_checkout_payment(
        session, UserId(user.id), amount, metadata=metadata, success_url=success_url, failure_url=failure_url
    )


async def delete_method(session: AsyncSession, user_id: UserId, method_id: str) -> bool:
    result = await dal.deactivate_method(session, user_id, method_id)
    await _commit(session)
    return result


async def _restore_reservation(session: AsyncSession, attempt) -> None:
    if attempt.reservation_released:
        return
    reserved = float((attempt.intent or {}).get("reserved_balance") or 0)
    if reserved:
        if attempt.user_id is None or await update_balance(session, UserId(attempt.user_id), reserved) is None:
            raise ValueError(texts.OVERPAY_AUTOPAY_RESERVATION_OWNER_MISSING)
    attempt.reservation_released = True
    await session.flush()


async def _cancel_attempt(session: AsyncSession, attempt) -> None:
    await _restore_reservation(session, attempt)
    _set_status(session, attempt, "canceled")
    row = await lock_payment_for_processing(session, attempt.payment_id)
    if row is not None and row.status not in {"success", "refunded", "chargebacked"}:
        await update_payment_status(session=session, internal_id=int(row.id), new_status="cancelled")
        attempt.payment_finalized = True
    elif attempt.intent.get("balance_only"):
        attempt.payment_finalized = True
    if attempt.subscription_id is not None:
        subscription = await dal.get_subscription(session, attempt.subscription_id, lock=True)
        if subscription is not None:
            subscription.retry_count = int(subscription.retry_count or 0) + 1
            subscription.next_payment_date = datetime.utcnow() + timedelta(
                hours=int(OVERPAY_AUTOPAY_CONFIG.get("RETRY_INTERVAL_HOURS", 24))
            )


async def _finalize_cancellation(attempt) -> None:
    if not attempt.intent.get("balance_only"):
        processed = await process_cancelled_payment(
            PROVIDER,
            ParsedPayment(attempt.payment_id, UserId(attempt.user_id), float(money(attempt.payload["amount"])), "RUB"),
        )
        if not processed.ok:
            return
    async with async_session_maker() as session:
        current = await dal.lock_attempt(session, attempt.id)
        if current is not None and current.status == "canceled":
            current.payment_finalized = True
            await _commit(session)


async def _submit_attempt(attempt_id: str) -> PipelineResult:
    async with async_session_maker() as session:
        attempt = await _lock_attempt_for_invoice(session, attempt_id)
        if attempt is None:
            return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_ATTEMPT_NOT_FOUND)
        if attempt.status in {"applied", "canceled"}:
            return PipelineResult(ok=True, already_processed=True)
        if attempt.status != "prepared":
            payment_id = attempt.payment_id
            await session.rollback()
            return await reconcile_payment(payment_id)
        if attempt.user_id is None or attempt.owner_deleted:
            attempt.review_reason = "Владелец платежа удалён"
            _set_status(session, attempt, "manual_review")
            await _commit(session)
            return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_PAYMENT_REVIEW_REQUIRED)
        if attempt.consent_canceled or not await is_overpay_autopay_enabled_for_session(session):
            await _cancel_attempt(session, attempt)
            await _commit(session)
            return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_NEW_PAYMENTS_DISABLED)
        if attempt.intent.get("balance_only"):
            _set_status(session, attempt, "succeeded")
            await _complete_attempt(session, UserId(attempt.user_id), 0, None, attempt_id=attempt_id)
            await _commit(session)
            return PipelineResult(ok=True)
        payload = copy.deepcopy(attempt.payload)
        if attempt.subscription_id is not None:
            subscription = await dal.get_subscription(session, attempt.subscription_id, lock=True)
            method = await dal.get_owned_method(session, attempt.user_id, attempt.method_id)
            if subscription is None or not subscription.is_active or method is None:
                await _cancel_attempt(session, attempt)
                await _commit(session)
                return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_CONSENT_CANCELED)
            if method.source_payment_id != attempt.intent.get("source_order_id"):
                await _paid_review(session, attempt, "Исходный заказ привязки изменился")
                await _commit(session)
                return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_METHOD_CHANGED)
        _set_status(session, attempt, "unknown")
        await _commit(session)

    async def before_send():
        return await _authorize_dispatch(attempt_id)

    if attempt.subscription_id is not None:
        result = await client.rebill(attempt.intent["source_order_id"], payload, before_send=before_send)
    else:
        result = await client.create_payment(payload, before_send=before_send)
    async with async_session_maker() as session:
        attempt = await _lock_attempt_for_invoice(session, attempt_id)
        if (
            attempt is None
            or attempt.status in {"applied", "succeeded", "canceled"}
            or attempt.status == "manual_review"
            and attempt.review_reason
        ):
            return PipelineResult(ok=True, already_processed=True)
        if result.data is not None:
            order_id = result.data.get("id")
            if not order_id:
                if attempt.subscription_id is not None:
                    await _paid_review(session, attempt, "В ответе rebill отсутствует номер списания")
                    await _commit(session)
                return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_ORDER_ID_UNCONFIRMED)
            if attempt.provider_order_id is not None and attempt.provider_order_id != str(order_id):
                return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_ORDER_ID_MISMATCH)
            order_id = str(order_id)
            collision = await dal.get_attempt_by_provider_order_id(session, order_id)
            if (
                not order_id.isascii()
                or not order_id.isdecimal()
                or len(order_id) > 64
                or order_id == attempt.intent.get("source_order_id")
                or collision is not None
                and collision.id != attempt.id
            ):
                await _paid_review(
                    session, attempt, "OverPay вернула неверный или повторно использованный номер заказа"
                )
                await _commit(session)
                return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_ORDER_ID_MISMATCH)
            attempt.provider_order_id = order_id
            location = result.data.get("resultUrl")
            if isinstance(location, str) and location:
                attempt.confirmation_url = _return_url(location)
            if attempt.subscription_id is not None and (
                str(order_id) == attempt.intent.get("source_order_id")
                or not _matches_attempt(attempt, result.data, webhook=False)
            ):
                await _paid_review(session, attempt, "OverPay вернула неожиданные параметры повторного списания")
            else:
                _set_status(session, attempt, "pending")
        elif not result.unknown:
            await _cancel_attempt(session, attempt)
        elif attempt.subscription_id is not None and attempt.dispatched_at is not None:
            await _paid_review(
                session, attempt, "Ответ rebill потерян: повторная отправка запрещена, нужна сверка OverPay"
            )
        await _commit(session)
    if result.data is not None and attempt.status == "pending" and attempt.subscription_id is not None:
        return await reconcile_payment(attempt.payment_id)
    return PipelineResult(ok=result.data is not None and attempt.status != "manual_review", error=result.error)


async def _lock_attempt_for_invoice(session, attempt_id):
    attempt = await dal.get_attempt(session, attempt_id)
    if attempt is None:
        return None
    await lock_payment_for_processing(session, attempt.payment_id)
    return await dal.lock_attempt(session, attempt_id)


async def _authorize_dispatch(attempt_id: str) -> bool:
    """Повторно проверяет согласие после ожидания очереди merchant API."""
    async with async_session_maker() as session:
        attempt = await _lock_attempt_for_invoice(session, attempt_id)
        if (
            attempt is None
            or attempt.status != "unknown"
            or attempt.dispatched_at is not None
            or attempt.owner_deleted
            or attempt.user_id is None
        ):
            return False
        allowed = not attempt.consent_canceled and await is_overpay_autopay_enabled_for_session(session)
        if attempt.subscription_id is not None:
            subscription = await dal.get_subscription(session, attempt.subscription_id, lock=True)
            method = await dal.get_owned_method(session, attempt.user_id, attempt.method_id)
            allowed = allowed and subscription is not None and subscription.is_active and method is not None
            if allowed:
                try:
                    key = await dal.get_owned_key(session, attempt.user_id, subscription.client_id, lock=True)
                    quote = await quote_autopay_for_key(session, UserId(attempt.user_id), subscription.client_id)
                    allowed = (
                        key is not None
                        and checkout_key_snapshot(key) == attempt.intent.get("key_snapshot")
                        and money(quote["amount"]) == money(attempt.intent["cost"])
                        and quote["period_days"] == attempt.intent["period_days"]
                        and quote["tariff_id"] == attempt.intent["tariff_id"]
                        and method.source_payment_id == attempt.intent.get("source_order_id")
                    )
                except ValueError:
                    allowed = False
        minimum = max(Decimal("10"), money(OVERPAY_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 10)))
        allowed = allowed and money(attempt.payload["amount"]) >= minimum
        if not allowed:
            await _cancel_attempt(session, attempt)
            await _commit(session)
            return False
        attempt.dispatched_at = datetime.utcnow()
        await _commit(session)
        return True


def _matches_attempt(attempt, data: dict, *, webhook: bool = False) -> bool:
    try:
        order_id = str(data.get("id") or "")
        if not order_id or str(data.get("currency") or "").upper() != "RUB":
            return False
        if money(data["amount"]) != money(attempt.payload["amount"]):
            return False
        if attempt.provider_order_id is not None and order_id != attempt.provider_order_id:
            return False
        if attempt.subscription_id is not None:
            return bool(attempt.provider_order_id and order_id != attempt.intent.get("source_order_id"))
        return data.get("merchant_order_id", data.get("merchantTransactionId")) == attempt.payment_id
    except (KeyError, ValueError, TypeError):
        return False


async def process_payment_webhook(data: dict) -> PipelineResult | None:
    """Использует уведомление только как повод для независимой сверки заказа."""
    order_id = str(data.get("id") or "")
    payment_id = str(data.get("merchantTransactionId") or "")
    async with async_session_maker() as session:
        attempt = await dal.get_attempt_by_provider_order_id(session, order_id) if order_id else None
        if attempt is None and payment_id.startswith("ovpa_"):
            candidate = await dal.get_attempt_by_payment_id(session, payment_id)
            if candidate is not None and candidate.subscription_id is None:
                if candidate.provider_order_id is not None and candidate.provider_order_id != order_id:
                    return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_PAYMENT_REVIEW_REQUIRED)
                attempt = candidate
        if attempt is None:
            if payment_id.startswith("ovpa_"):
                return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_PAYMENT_REVIEW_REQUIRED)
            return None
    return await reconcile_payment(attempt.payment_id)


async def _process_confirmed(attempt) -> PipelineResult:
    if attempt.user_id is None or attempt.owner_deleted:
        return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_PAID_OWNER_DELETED)
    parsed = ParsedPayment(attempt.payment_id, UserId(attempt.user_id), float(money(attempt.payload["amount"])), "RUB")

    async def completion(session, owner, amount, row):
        return await _complete_attempt(session, owner, amount, row, attempt_id=attempt.id)

    return await process_success_payment(PROVIDER, parsed, completion=completion, use_temporary_checkout=False)


def _settlement_matches(attempt, data: dict, status: str) -> bool:
    """Сверяет фактически списанную и возвращённую сумму заказа."""
    try:
        expected = money(attempt.payload["amount"])
        charged = money(data["amount_charged"], positive=False)
        refunded = money(data["amount_refunded"], positive=False)
        if charged != expected:
            return False
        if status == "refunded":
            return refunded == expected
        if refunded != 0:
            return False
        if status == "chargeback":
            operations = data["operations"]
            if not isinstance(operations, list):
                return False
            reversed_amount = Decimal("0")
            for operation in operations:
                if not isinstance(operation, dict):
                    return False
                if operation.get("type") == "chargeback" and operation.get("status") == "success":
                    if str(operation.get("currency") or "").upper() != "RUB":
                        return False
                    reversed_amount += money(operation["amount"])
            return reversed_amount == expected
        return status == "charged"
    except (KeyError, ValueError, TypeError):
        return False


async def _review_settlement(attempt_id: str) -> PipelineResult:
    """Останавливает автоматическую обработку неоднозначной финансовой операции."""
    async with async_session_maker() as session:
        attempt = await _lock_attempt_for_invoice(session, attempt_id)
        if attempt is None:
            return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_ATTEMPT_NOT_FOUND)
        if not attempt.review_reason:
            if attempt.subscription_id is None and attempt.method_id is not None and attempt.user_id is not None:
                await dal.deactivate_method(session, attempt.user_id, attempt.method_id)
            await _paid_review(session, attempt, "Фактическая сумма списания или возврата требует сверки OverPay")
            await _commit(session)
    await _notify_review(attempt_id)
    return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_PAYMENT_REVIEW_REQUIRED)


async def reconcile_payment(payment_id: str) -> PipelineResult:
    async with async_session_maker() as session:
        attempt = await dal.get_attempt_by_payment_id(session, payment_id)
    if attempt is None:
        return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_ORDER_NOT_FOUND)
    if attempt.status == "canceled":
        return PipelineResult(ok=True, already_processed=True)
    if attempt.status == "prepared":
        return await _submit_attempt(attempt.id)
    if attempt.review_reason:
        if attempt.payment_finalized:
            return PipelineResult(ok=True, already_processed=True)
        return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_MANUAL_REVIEW_REQUIRED)
    if attempt.subscription_id is not None and attempt.provider_order_id is None:
        started_at = attempt.dispatched_at or attempt.updated_at or attempt.created_at
        if started_at and datetime.utcnow() < started_at + timedelta(
            seconds=int(OVERPAY_AUTOPAY_CONFIG.get("API_TIMEOUT", 30)) + 60
        ):
            return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_REQUEST_PENDING)
        async with async_session_maker() as session:
            current = await _lock_attempt_for_invoice(session, attempt.id)
            if current is not None and current.status not in {"applied", "canceled"}:
                await _paid_review(session, current, "Неизвестен номер списания rebill; автоматический повтор запрещён")
                await _commit(session)
        return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_PAYMENT_REVIEW_REQUIRED)
    result = await client.get_payment(payment_id, provider_order_id=attempt.provider_order_id)
    if result.data is None:
        return PipelineResult(ok=False, error=result.error)
    if not _matches_attempt(attempt, result.data):
        return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_ORDER_MISMATCH)
    status = str(result.data.get("status") or "").lower()
    if status in {"charged", "refunded", "chargeback"} and not _settlement_matches(attempt, result.data, status):
        return await _review_settlement(attempt.id)
    async with async_session_maker() as session:
        current = await _lock_attempt_for_invoice(session, attempt.id)
        if current.provider_order_id is None:
            current.provider_order_id = str(result.data["id"])
            await _commit(session)
    attempt.provider_order_id = str(result.data["id"])
    if status == "charged":
        return await _process_confirmed(attempt)
    if status in {"declined", "rejected", "reversed", "error"}:
        parsed = ParsedPayment(
            payment_id,
            UserId(attempt.user_id) if attempt.user_id else None,
            float(money(attempt.payload["amount"])),
            "RUB",
        )
        processed = await process_cancelled_payment(PROVIDER, parsed)
        if processed.ok:
            async with async_session_maker() as session:
                current = await _lock_attempt_for_invoice(session, attempt.id)
                if current and current.status not in {"applied", "canceled", "succeeded", "manual_review"}:
                    await _cancel_attempt(session, current)
                    await _commit(session)
        return processed
    if status in {"refunded", "chargeback"}:
        parsed = ParsedPayment(
            payment_id,
            UserId(attempt.user_id) if attempt.user_id else None,
            float(money(attempt.payload["amount"])),
            "RUB",
        )
        processed = await process_cancelled_payment(
            PROVIDER, parsed, new_status="refunded" if status == "refunded" else "chargebacked"
        )
        if not processed.ok:
            return processed
        async with async_session_maker() as session:
            current = await _lock_attempt_for_invoice(session, attempt.id)
            await _restore_reservation(session, current)
            if current.subscription_id is None and current.method_id is not None and current.user_id is not None:
                await dal.deactivate_method(session, current.user_id, current.method_id)
            await _paid_review(session, current, "Возврат или оспаривание списания требует сверки")
            current.payment_finalized = True
            await _commit(session)
        return PipelineResult(ok=True)
    return PipelineResult(ok=False, error=texts.OVERPAY_AUTOPAY_PAYMENT_PENDING)


async def _paid_review(session: AsyncSession, attempt, reason: str) -> None:
    attempt.review_reason = reason
    _set_status(session, attempt, "manual_review")
    if attempt.subscription_id is not None and attempt.user_id is not None:
        await dal.cancel_subscription(session, UserId(attempt.user_id), attempt.subscription_id)


async def _adopt_checkout_consent(session: AsyncSession, attempt) -> None:
    intent = attempt.intent or {}
    metadata = intent.get("metadata") or {}
    if attempt.autopay_consent_applied or metadata.get("payment_flow") in {"gift_create", "key_addons", "addons"}:
        return
    client_id = attempt.fulfilled_client_id or metadata.get("autopay_client_id")
    if not (
        client_id
        and attempt.method_id
        and not attempt.consent_canceled
        and intent.get("autopay_consent") is True
        and await is_overpay_autopay_enabled_for_session(session)
    ):
        return
    if await dal.get_owned_subscription(session, attempt.user_id, client_id):
        return
    amount, period = metadata.get("autopay_accepted_amount"), metadata.get("autopay_accepted_period_days")
    if amount is None or period is None:
        return
    try:
        await enable_autopay_for_key(
            session,
            UserId(attempt.user_id),
            client_id,
            attempt.method_id,
            consent={"accepted": True, "amount": amount, "period_days": period, "source": "paid_checkout"},
        )
        attempt.autopay_consent_applied = True
    except ValueError:
        logger.info("OverPay: условия автопродления требуют повторного согласия, attempt={}", attempt.id)


async def _complete_attempt(session, owner, amount, payment_row, *, attempt_id: str) -> CheckoutCompletion | str | None:
    attempt = await dal.lock_attempt(session, attempt_id)
    if attempt is None or attempt.user_id != int(owner) or attempt.owner_deleted:
        raise ValueError(texts.OVERPAY_AUTOPAY_OWNER_CHANGED)
    if attempt.status == "applied":
        return
    if attempt.review_reason:
        raise ValueError(texts.OVERPAY_AUTOPAY_MANUAL_REVIEW_REQUIRED)
    if attempt.status == "canceled":
        raise ValueError(texts.OVERPAY_AUTOPAY_ORDER_CANCELED)
    _set_status(session, attempt, "succeeded")
    await _restore_reservation(session, attempt)
    intent = attempt.intent or {}
    completed_flow = None
    completed_client_id = None
    if intent.get("kind") == "recurring":
        key = await dal.get_owned_key(session, owner, intent["client_id"], lock=True)
        if key is None or key.is_frozen or checkout_key_snapshot(key) != intent.get("key_snapshot"):
            await _paid_review(session, attempt, "Подписка изменилась после создания автоплатежа")
            return
        if not await get_tariff_by_id(session, int(intent["tariff_id"])):
            await _paid_review(session, attempt, "Оплаченный тариф больше не существует")
            return
        final_expiry = max(normalize_expiry_ms(key.expiry_time), int(datetime.now(UTC).timestamp() * 1000))
        final_expiry += int(intent["period_days"]) * 86400000
        try:
            async with session.begin_nested():
                await execute_renewal(
                    session,
                    billing_user_id=owner,
                    client_id=key.client_id,
                    key_email=key.email,
                    key_server_id=key.server_id,
                    tariff_id=int(intent["tariff_id"]),
                    new_expiry_time=final_expiry,
                    total_gb=int(intent.get("selected_traffic_limit") or 0),
                    cost=float(intent["cost"]),
                    selected_device_limit=intent.get("selected_device_limit"),
                    selected_traffic_limit=intent.get("selected_traffic_limit"),
                    selected_price_rub=int(round(float(intent["cost"]))),
                    expected_expiry_time=normalize_expiry_ms(key.expiry_time),
                    expected_key_snapshot=intent["key_snapshot"],
                )
        except ServiceError as exc:
            await _paid_review(session, attempt, str(exc))
            return
        completed_flow, completed_client_id = "key_renewal", key.client_id
        subscription = await dal.get_subscription(session, attempt.subscription_id, lock=True)
        if subscription is not None:
            subscription.next_payment_date = _next_payment_date(final_expiry, int(intent["period_days"]))
            subscription.retry_count = 0
    else:
        if attempt.method_id is None and intent.get("autopay_consent") is True:
            method = await dal.save_verified_method(session, attempt.user_id, attempt.provider_order_id)
            attempt.method_id = method.id
            if attempt.consent_canceled:
                method.is_active, method.deleted_at = False, datetime.utcnow()
        checkout = intent.get("checkout") or {}
        try:
            if checkout.get("state") != "balance_topup":
                checkout = await prepare_checkout_completion(session, owner, checkout)
            async with session.begin_nested():
                attempt.fulfilled_client_id = await apply_checkout_intent(session, owner, checkout, amount)
        except ServiceError as exc:
            await _paid_review(session, attempt, str(exc))
            return
        await _adopt_checkout_consent(session, attempt)
        completed_flow, completed_client_id = checkout_flow(checkout), attempt.fulfilled_client_id
    _set_status(session, attempt, "applied")
    attempt.payment_finalized = True
    await session.flush()

    if completed_client_id and completed_flow in {"tariff_purchase", "trial_purchase", "key_renewal"}:
        return CheckoutCompletion(completed_flow, completed_client_id)
    return completed_flow if completed_flow != "balance_topup" else None


async def _prepare_subscription(subscription_id: int) -> str | None:
    async with async_session_maker() as session:
        subscription = await dal.get_subscription(session, subscription_id, lock=True)
        if (
            subscription is None
            or subscription.user_id is None
            or not subscription.is_active
            or subscription.next_payment_date > datetime.utcnow()
            or not await is_overpay_autopay_enabled_for_session(session)
        ):
            return None
        uid = UserId(subscription.user_id)
        existing = await dal.get_open_subscription_attempt(session, subscription_id)
        if existing:
            return existing.id
        if subscription.retry_count >= int(OVERPAY_AUTOPAY_CONFIG.get("MAX_RETRY_ATTEMPTS", 3)):
            subscription.is_active = False
            await _commit(session)
            return None
        try:
            key = await dal.get_owned_key(session, uid, subscription.client_id, lock=True)
            quote = await quote_autopay_for_key(session, uid, subscription.client_id)
        except ValueError:
            subscription.is_active = False
            await _commit(session)
            return None
        method = await dal.get_owned_method(session, uid, subscription.method_id)
        if (
            method is None
            or money(quote["amount"]) > money(subscription.amount)
            or quote["period_days"] != subscription.period_days
            or quote["tariff_id"] != subscription.tariff_id
        ):
            subscription.is_active = False
            await _commit(session)
            return None
        due = _next_payment_date(quote["expiry_ms"], quote["period_days"])
        if due > datetime.utcnow():
            subscription.next_payment_date = due
            await _commit(session)
            return None
        user = await dal.get_owner(session, uid, lock=True)
        try:
            balance = money(user.balance or 0, positive=False)
        except ValueError:
            subscription.is_active = False
            await _commit(session)
            return None
        cost = money(quote["amount"])
        reserved = min(balance, cost)
        charge = cost - reserved
        minimum = max(Decimal("10"), money(OVERPAY_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 10)))
        if Decimal("0") < charge < minimum:
            charge = min(cost, minimum)
            reserved = cost - charge
        attempt_id = str(uuid.uuid4())
        payment_id = f"ovpa_{uuid.UUID(attempt_id).hex}"
        intent = {
            "kind": "recurring",
            "original_user_id": int(uid),
            **quote,
            "cost": float(cost),
            "key_snapshot": checkout_key_snapshot(key),
            "reserved_balance": float(reserved),
            "balance_only": charge == 0,
            "source_order_id": method.source_payment_id,
        }
        attempt = await dal.prepare_attempt(
            session,
            uid,
            attempt_id=attempt_id,
            payment_id=payment_id,
            subscription_id=subscription.id,
            method_id=method.id,
            payload={"amount": str(charge), "location": {"ip": str(OVERPAY_SERVER_IP).strip()}},
            intent=intent,
        )
        if reserved and await update_balance(session, uid, -float(reserved)) is None:
            raise ValueError(texts.OVERPAY_AUTOPAY_RESERVATION_FAILED)
        if charge:
            await add_payment(
                session,
                user_id=uid,
                amount=float(charge),
                payment_system=PROVIDER,
                payment_id=payment_id,
                status="pending",
                currency="RUB",
                metadata={"autopay_attempt_id": attempt_id},
            )
        await _commit(session)
        return attempt.id


async def _notify_review(attempt_id: str) -> None:
    async with async_session_maker() as session:
        attempt = await dal.lock_attempt(session, attempt_id)
        if attempt is None or attempt.notice_sent or not attempt.review_reason:
            return
        attempt.notice_sent = True
        if attempt.user_id is not None:
            await notify_web(
                session,
                user_ref=UserId(attempt.user_id),
                type="payment",
                title=texts.OVERPAY_AUTOPAY_REVIEW_TITLE,
                message=texts.OVERPAY_AUTOPAY_REVIEW_MESSAGE,
                data={"href": "/dashboard/subscriptions", "payment_id": attempt.payment_id},
            )
        await _commit(session)
    await send_admin_alert(f"OverPay: требуется проверка автоплатежа {attempt_id}. Новое списание заблокировано.")


async def run_once(bot=None, sessionmaker=async_session_maker) -> None:
    """Сверяет отправленные платежи и запускает согласованные продления."""
    async with sessionmaker() as session:
        attempts = await dal.list_unfinished_attempts(session)
        cancellations = await dal.list_unfinalized_cancellations(session)
        due = await dal.list_due_subscriptions(session)
    for attempt in attempts:
        try:
            if attempt.status == "prepared":
                await _submit_attempt(attempt.id)
            else:
                await reconcile_payment(attempt.payment_id)
            await _notify_review(attempt.id)
        except Exception:
            logger.error("OverPay: не удалось сверить попытку {}", attempt.id)
    for subscription in due:
        try:
            attempt_id = await _prepare_subscription(subscription.id)
            if attempt_id:
                await _submit_attempt(attempt_id)
                await _notify_review(attempt_id)
        except Exception:
            logger.error("OverPay: не удалось обработать подписку {}", subscription.id)
    for attempt in cancellations:
        try:
            await _finalize_cancellation(attempt)
        except Exception:
            logger.error("OverPay: не удалось завершить отмену {}", attempt.id)


async def autopay_loop(bot, sessionmaker=async_session_maker) -> None:
    while True:
        try:
            await run_once(bot, sessionmaker)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("OverPay: ошибка фоновой проверки автоплатежей")
        await asyncio.sleep(60)
