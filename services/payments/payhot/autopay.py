import asyncio
import copy
import uuid

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.payhot_autopay_config import (
    PAYHOT_AUTOPAY_CONFIG,
    is_payhot_autopay_enabled,
    is_payhot_autopay_enabled_for_session,
)
from database import (
    async_session_maker,
    payhot_autopay as dal,
)
from database.access.resolution import UserId
from database.cache_purge import flush_purges
from database.models import Payment
from database.models.payhot_autopay import PayHotAutopayAttempt, PayHotAutopayLog, PayHotSavedMethod
from database.payments import add_payment, lock_payment_for_processing, require_payment_provider, update_payment_status
from database.tariffs import get_tariff_by_id
from database.users import update_balance
from logger import logger
from services.admin_alert import send_admin_alert
from services.errors import ServiceError
from services.keys import execute_renewal, normalize_expiry_ms
from services.payments.checkout_intent import (
    apply_checkout_intent,
    autopay_terms_for_tariff,
    checkout_flow,
    checkout_key_snapshot,
    prepare_checkout_completion,
)
from services.payments.payhot.api import PaymentError, api_request, rub_amount
from services.payments.pipeline import CheckoutCompletion, ParsedPayment, PipelineResult, process_success_payment
from services.renewal_access import resolve_key_renewal_scope
from settings import texts


PROVIDER = "PAYHOT_AUTOPAY"


def get_config() -> dict:
    """Возвращает текущие настройки автопродления."""
    return {**PAYHOT_AUTOPAY_CONFIG, "ENABLED": is_payhot_autopay_enabled()}


def money(value) -> Decimal:
    """Проверяет точность и конечность суммы в рублях."""
    try:
        result = Decimal(str(value))
        if (
            isinstance(value, bool)
            or not result.is_finite()
            or result < 0
            or result != result.quantize(Decimal("0.01"))
        ):
            raise ValueError
        return result.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(texts.PAYHOT_AMOUNT_INVALID) from exc


def _identifier(value) -> str:
    """Проверяет идентификатор перед использованием в адресе API."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(not (char.isascii() and (char.isalnum() or char in "_-")) for char in value)
    ):
        raise PaymentError(texts.PAYHOT_AUTOPAY_METHOD_INVALID)
    return value


def validate_mandate(
    mandate: dict, mandate_id: str, payment_method: str, reference: str | None = None, *, active: bool = True
) -> dict:
    """Сверяет разрешение с проверенным платежом и сохранённой привязкой."""
    if (
        not isinstance(mandate, dict)
        or mandate.get("environment") == "sandbox"
        or mandate.get("id") != mandate_id
        or mandate.get("payment_method") != payment_method
        or payment_method not in {"card", "sbp"}
        or mandate.get("currency") != "RUB"
        or not isinstance(mandate.get("merchant_mandate_reference"), str)
        or not mandate["merchant_mandate_reference"].strip()
        or len(mandate["merchant_mandate_reference"]) > 255
        or reference is not None
        and mandate["merchant_mandate_reference"] != reference
        or active
        and mandate.get("status") != "active"
    ):
        raise PaymentError(texts.PAYHOT_AUTOPAY_METHOD_INVALID)
    return mandate


async def verified_first_enrollment(payment: dict, invoice: dict) -> dict | None:
    """Проверяет привязку только по сверённому успешному платежу."""
    metadata = invoice.get("metadata") or {}
    payload = metadata.get("payhot_request") or {}
    if (
        metadata.get("autopay_consent") is not True
        or payload.get("recurring_enrollment") != {}
        or payment.get("status") != "succeeded"
    ):
        return None
    if (payload.get("customer") or {}).get("id") != f"u{invoice['user_id']}":
        raise PaymentError(texts.PAYHOT_AUTOPAY_METHOD_OWNER_MISMATCH)
    enrollment = payment.get("recurring_enrollment") or {}
    if enrollment.get("status") != "active":
        return None
    mandate_id = _identifier(enrollment.get("mandate_id"))
    response = await api_request("GET", f"/mandates/{mandate_id}")
    return validate_mandate(response.get("mandate"), mandate_id, payload.get("payment_method"))


async def quote_autopay_for_key(session: AsyncSession, user_id: UserId, client_id: str) -> dict:
    """Рассчитывает прежние условия продления существующей подписки."""
    key = await dal.get_owned_key(session, UserId(user_id), client_id)
    if key is None or key.is_frozen:
        raise ValueError(texts.PAYHOT_AUTOPAY_SUBSCRIPTION_UNAVAILABLE)
    tariff = await get_tariff_by_id(session, key.tariff_id)
    if not tariff:
        raise ValueError(texts.PAYHOT_AUTOPAY_TARIFF_NOT_FOUND)
    try:
        await resolve_key_renewal_scope(session, key, tariff)
    except ServiceError as exc:
        raise ValueError(exc.message) from exc
    terms = autopay_terms_for_tariff(
        tariff, key.selected_device_limit, key.selected_traffic_limit, provider_id=PROVIDER
    )
    if not terms or money(terms["amount"]) < money(PAYHOT_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)):
        raise ValueError(texts.PAYHOT_AUTOPAY_TARIFF_UNAVAILABLE)
    amount = rub_amount(terms["amount"])
    return {
        "amount": float(amount),
        "period_days": int(terms["period_days"]),
        "tariff_id": int(key.tariff_id),
        "label": tariff.get("name") or texts.PAYHOT_AUTOPAY_SUBSCRIPTION_LABEL,
        "client_id": key.client_id,
        "key_email": key.email,
        "expiry_ms": normalize_expiry_ms(key.expiry_time),
        "server_id": key.server_id,
        "selected_device_limit": key.selected_device_limit,
        "selected_traffic_limit": key.selected_traffic_limit,
    }


def _next_payment_date(expiry_ms: int, period_days: int) -> datetime:
    """Ограничивает раннее списание длительностью периода."""
    lead = min(int(PAYHOT_AUTOPAY_CONFIG.get("DAYS_BEFORE_EXPIRY", 3)), max(0, period_days - 1))
    return datetime.utcfromtimestamp(expiry_ms / 1000) - timedelta(days=lead)


async def enable_autopay_for_key(
    session: AsyncSession, user_id: UserId, client_id: str, method_id: str | None = None, *, consent: dict | None = None
):
    """Включает продление по проверенному разрешению и явному согласию."""
    if not await is_payhot_autopay_enabled_for_session(session):
        raise ValueError(texts.PAYHOT_AUTOPAY_NEW_PAYMENTS_DISABLED)
    if not isinstance(consent, dict) or consent.get("accepted") is not True:
        raise ValueError(texts.PAYHOT_AUTOPAY_RECURRING_CONSENT_REQUIRED)
    if consent.get("amount") is None or consent.get("period_days") is None:
        raise ValueError(texts.PAYHOT_AUTOPAY_TERMS_CONSENT_REQUIRED)
    await dal.get_owner(session, user_id, lock=True)
    quote = await quote_autopay_for_key(session, user_id, client_id)
    if money(quote["amount"]) > money(consent["amount"]):
        raise ValueError(texts.PAYHOT_AUTOPAY_AMOUNT_CHANGED)
    if quote["period_days"] != int(consent["period_days"]):
        raise ValueError(texts.PAYHOT_AUTOPAY_PERIOD_CHANGED)
    method = (
        await dal.get_owned_method(session, user_id, method_id)
        if method_id
        else await dal.get_active_method(session, user_id)
    )
    if method is None:
        raise ValueError(texts.PAYHOT_AUTOPAY_METHOD_REQUIRED)
    from services.payments.payhot.service import available

    if not available(
        "cards" if method.payment_method == "card" else "sbp"
    ) or not await is_payhot_autopay_enabled_for_session(session, payment_method=method.payment_method):
        raise ValueError(texts.PAYHOT_AUTOPAY_UNAVAILABLE)
    response = await api_request("GET", f"/mandates/{_identifier(method.mandate_id)}")
    validate_mandate(
        response.get("mandate"), method.mandate_id, method.payment_method, method.merchant_mandate_reference
    )
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


async def cancel_subscription(session: AsyncSession, user_id: UserId, subscription_id: int) -> bool:
    """Останавливает новые списания и оставляет отправленные на сверке."""
    return await dal.cancel_subscription(session, UserId(user_id), subscription_id)


async def delete_method(session: AsyncSession, user_id: UserId, method_id: str) -> bool:
    """Сначала отзывает локальное согласие, затем разрешение кассы."""
    await dal.get_owner(session, UserId(user_id), lock=True)
    method = await session.scalar(
        select(PayHotSavedMethod).where(PayHotSavedMethod.user_id == int(user_id), PayHotSavedMethod.id == method_id)
    )
    if method is None:
        return False
    await dal.deactivate_method(session, user_id, method_id)
    await _commit(session)
    await _revoke_method(session, method)
    return True


async def _revoke_method(session: AsyncSession, method) -> None:
    """Подтверждает отзыв разрешения, включая удалённого владельца."""
    response = await api_request(
        "POST", f"/mandates/{_identifier(method.mandate_id)}/revoke", payload={"reason": "consent_withdrawn"}
    )
    mandate = validate_mandate(
        response.get("mandate"),
        method.mandate_id,
        method.payment_method,
        method.merchant_mandate_reference,
        active=False,
    )
    if mandate.get("status") != "revoked":
        raise PaymentError(texts.PAYHOT_AUTOPAY_METHOD_INVALID, retryable=True)
    method.revoked_at = datetime.utcnow()
    await session.flush()


async def create_link(session, user_ref, amount, currency, success_url, failure_url, metadata):
    """Создаёт обычный счёт PayHOT с подтверждаемой привязкой."""
    from services.payments.payhot.service import create_invoice

    if str(currency).upper() != "RUB":
        raise PaymentError(texts.PAYHOT_CURRENCY_UNSUPPORTED)
    metadata = dict(metadata or {})
    if metadata.get("autopay_consent") is not True:
        raise PaymentError(texts.PAYHOT_AUTOPAY_RECURRING_CONSENT_REQUIRED)
    method = metadata.get("payhot_method")
    if method not in {"card", "cards", "sbp"}:
        raise PaymentError(texts.PAYHOT_METHOD_UNAVAILABLE)
    return await create_invoice(
        session, user_ref, amount, "cards" if method in {"card", "cards"} else "sbp", metadata=metadata
    )


async def complete_first_payment(session: AsyncSession, owner: UserId, amount: float, row) -> CheckoutCompletion | None:
    """Однократно применяет покупку и подключает подтверждённое разрешение."""
    if row is None or row.user_id != int(owner):
        raise ValueError(texts.PAYHOT_AUTOPAY_OWNER_CHANGED)
    require_payment_provider(row, "payhot")
    metadata = copy.deepcopy(row.metadata_ or {})
    if metadata.get("autopay_consent") is not True:
        return None
    method = None
    mandate = metadata.get("payhot_verified_mandate")
    if isinstance(mandate, dict):
        method = await dal.save_verified_method(session, owner, mandate, row.payment_id)
    checkout = metadata.get("payhot_checkout_intent") or {}
    if not metadata.get("payhot_checkout_completed") and not metadata.get("payhot_checkout_review"):
        try:
            prepared = await prepare_checkout_completion(session, owner, checkout)
            async with session.begin_nested():
                client_id = await apply_checkout_intent(session, owner, prepared, amount)
            metadata.update(payhot_checkout_completed=True, payhot_fulfilled_client_id=client_id)
        except ServiceError as exc:
            metadata["payhot_checkout_review"] = str(exc)
    client_id = metadata.get("payhot_fulfilled_client_id")
    if (
        method is not None
        and client_id
        and not metadata.get("payhot_autopay_consent_applied")
        and not metadata.get("payhot_autopay_requires_consent")
    ):
        try:
            existing = await dal.get_owned_subscription(session, owner, client_id)
            if existing is None:
                await enable_autopay_for_key(
                    session,
                    owner,
                    client_id,
                    method.id,
                    consent={
                        "accepted": True,
                        "amount": metadata.get("autopay_accepted_amount"),
                        "period_days": metadata.get("autopay_accepted_period_days"),
                        "source": "paid_checkout",
                    },
                )
            metadata["payhot_autopay_consent_applied"] = True
        except ValueError:
            metadata["payhot_autopay_requires_consent"] = True
    enrollment = metadata.get("payhot_recurring_enrollment") or {}
    metadata["payhot_enrollment_pending"] = bool(
        not metadata.get("payhot_autopay_consent_applied")
        and not metadata.get("payhot_autopay_requires_consent")
        and method is None
        and enrollment.get("status") in {None, "pending", "active"}
    )
    row.metadata_ = metadata
    await session.flush()
    flow = checkout_flow(checkout)
    return CheckoutCompletion(flow, client_id) if client_id else None


async def _commit(session: AsyncSession) -> None:
    """Сохраняет изменения и сбрасывает накопленные кэши."""
    await session.commit()
    await flush_purges(session)


def _set_status(session: AsyncSession, attempt, status: str) -> None:
    """Фиксирует переход состояния в журнале попытки."""
    if attempt.status != status:
        attempt.status = status
        session.add(PayHotAutopayLog(attempt_id=attempt.id, status=status, reason=attempt.review_reason))


async def _restore_reservation(session: AsyncSession, attempt) -> None:
    """Однократно возвращает зарезервированную часть баланса."""
    if attempt.reservation_released:
        return
    reserved = money((attempt.intent or {}).get("reserved_balance") or 0)
    if reserved and (
        attempt.user_id is None or await update_balance(session, UserId(attempt.user_id), float(reserved)) is None
    ):
        raise ValueError(texts.PAYHOT_AUTOPAY_RESERVATION_FAILED)
    attempt.reservation_released = True


async def _cancel_attempt(session: AsyncSession, attempt) -> None:
    """Освобождает резерв и завершает неподтверждённое списание."""
    if attempt.status == "canceled":
        return
    if attempt.user_id is None or attempt.owner_deleted:
        await _review(session, attempt, "Возврат резерва удалённого владельца требует ручной сверки")
        return
    await _restore_reservation(session, attempt)
    _set_status(session, attempt, "canceled")
    if not attempt.intent.get("balance_only"):
        row = await lock_payment_for_processing(session, attempt.payment_id)
        if row is not None and row.status not in {"success", "refunded", "chargebacked"}:
            await update_payment_status(session=session, internal_id=int(row.id), new_status="cancelled")
    attempt.payment_finalized = True
    subscription = await dal.get_subscription(session, attempt.subscription_id)
    if subscription is not None:
        subscription.retry_count += 1
        subscription.next_payment_date = datetime.utcnow() + timedelta(
            hours=int(PAYHOT_AUTOPAY_CONFIG.get("RETRY_INTERVAL_HOURS", 24))
        )


async def _review(session: AsyncSession, attempt, reason: str) -> None:
    """Останавливает будущие списания для ручного разбора."""
    attempt.review_reason = reason
    _set_status(session, attempt, "manual_review")
    subscription = await dal.get_subscription(session, attempt.subscription_id)
    if subscription is not None:
        subscription.is_active = False


async def _lock_attempt(session: AsyncSession, attempt_id: str):
    """Соблюдает общий порядок блокировок платежа и попытки."""
    attempt = await session.get(PayHotAutopayAttempt, attempt_id)
    if attempt is not None and not attempt.intent.get("balance_only"):
        await lock_payment_for_processing(session, attempt.payment_id)
    return await dal.lock_attempt(session, attempt_id)


async def _complete_attempt(session: AsyncSession, owner: UserId, amount, row, *, attempt_id: str):
    """Применяет один подтверждённый платёж без повторного зачисления."""
    attempt = await _lock_attempt(session, attempt_id)
    if attempt is None or attempt.user_id != int(owner) or attempt.owner_deleted:
        raise ValueError(texts.PAYHOT_AUTOPAY_OWNER_CHANGED)
    if attempt.status == "applied":
        return None
    await _restore_reservation(session, attempt)
    if attempt.review_reason:
        return None
    intent = attempt.intent
    key = await dal.get_owned_key(session, owner, intent["client_id"], lock=True)
    if key is None or key.is_frozen or checkout_key_snapshot(key) != intent.get("key_snapshot"):
        await _review(session, attempt, "Подписка изменилась после создания автоплатежа")
        return None
    final_expiry = (
        max(normalize_expiry_ms(key.expiry_time), int(datetime.now(UTC).timestamp() * 1000))
        + int(intent["period_days"]) * 86400000
    )
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
        await _review(session, attempt, str(exc))
        return None
    subscription = await dal.get_subscription(session, attempt.subscription_id)
    if subscription is not None:
        subscription.next_payment_date = _next_payment_date(final_expiry, int(intent["period_days"]))
        subscription.retry_count = 0
    _set_status(session, attempt, "applied")
    attempt.payment_finalized = True
    await session.flush()
    return CheckoutCompletion("key_renewal", key.client_id)


def _validate_charge(charge: dict, attempt) -> str:
    """Сверяет все неизменные реквизиты повторного списания."""
    if not isinstance(charge, dict) or charge.get("environment") == "sandbox":
        raise PaymentError(texts.PAYHOT_PAYMENT_INVALID)
    charge_id = _identifier(charge.get("id"))
    if (
        attempt.provider_charge_id not in {None, charge_id}
        or any(
            charge.get(field) != attempt.payload.get(field)
            for field in (
                "merchant_recurring_charge_reference",
                "mandate_id",
                "amount_minor",
                "currency",
                "payment_method",
            )
        )
        or charge.get("status") not in {"processing", "succeeded", "failed", "cancelled"}
    ):
        raise PaymentError(texts.PAYHOT_PAYMENT_MISMATCH)
    return charge_id


async def _dispatch(attempt_id: str) -> PipelineResult:
    """Отправляет только сохранённый запрос с постоянным ключом операции."""
    async with async_session_maker() as session:
        attempt = await _lock_attempt(session, attempt_id)
        if attempt is None or attempt.status in {"applied", "canceled"}:
            return PipelineResult(ok=True, already_processed=True)
        if attempt.status not in {"prepared", "unknown"}:
            return PipelineResult(ok=True)
        if attempt.owner_deleted or attempt.user_id is None:
            await _review(session, attempt, "Владелец автоплатежа удалён")
            await _commit(session)
            return PipelineResult(ok=False, error=texts.PAYHOT_AUTOPAY_OWNER_CHANGED)
        if attempt.dispatch_attempts >= int(PAYHOT_AUTOPAY_CONFIG.get("MAX_RETRY_ATTEMPTS", 3)):
            await _review(session, attempt, "Ответ повторного списания не подтверждён после повторов с тем же ключом")
            await _commit(session)
            return PipelineResult(ok=False, error=texts.PAYHOT_AUTOPAY_PAYMENT_REVIEW_REQUIRED)
        if attempt.dispatched_at is None:
            subscription = await dal.get_subscription(session, attempt.subscription_id)
            method = (
                await dal.get_owned_method(session, attempt.user_id, attempt.method_id) if attempt.user_id else None
            )
            allowed = bool(
                not attempt.owner_deleted
                and not attempt.consent_canceled
                and subscription
                and subscription.is_active
                and method
                and await is_payhot_autopay_enabled_for_session(session, payment_method=method.payment_method)
            )
            if allowed:
                try:
                    quote = await quote_autopay_for_key(session, UserId(attempt.user_id), subscription.client_id)
                    key = await dal.get_owned_key(session, attempt.user_id, subscription.client_id)
                    from services.payments.payhot.service import available

                    allowed = (
                        available("cards" if method.payment_method == "card" else "sbp")
                        and money(quote["amount"]) == money(attempt.intent["cost"])
                        and quote["period_days"] == attempt.intent["period_days"]
                        and quote["tariff_id"] == attempt.intent["tariff_id"]
                        and checkout_key_snapshot(key) == attempt.intent["key_snapshot"]
                        and method.mandate_id == attempt.payload["mandate_id"]
                    )
                    if allowed and not attempt.intent.get("balance_only"):
                        response = await api_request("GET", f"/mandates/{_identifier(method.mandate_id)}")
                        validate_mandate(
                            response.get("mandate"),
                            method.mandate_id,
                            method.payment_method,
                            method.merchant_mandate_reference,
                        )
                except ValueError:
                    allowed = False
            if not allowed:
                await _cancel_attempt(session, attempt)
                await _commit(session)
                return PipelineResult(ok=False, error=texts.PAYHOT_AUTOPAY_NEW_PAYMENTS_DISABLED)
            if attempt.intent.get("balance_only"):
                _set_status(session, attempt, "succeeded")
                await _complete_attempt(session, UserId(attempt.user_id), 0, None, attempt_id=attempt.id)
                await _commit(session)
                return PipelineResult(ok=True)
            attempt.dispatched_at = datetime.utcnow()
        _set_status(session, attempt, "unknown")
        attempt.dispatch_attempts += 1
        payload, payment_id = copy.deepcopy(attempt.payload), attempt.payment_id
        await _commit(session)
    try:
        response = await api_request("POST", "/recurring-charges", payload=payload, idempotency_key=payment_id)
        charge = response.get("recurring_charge")
    except PaymentError as exc:
        if exc.rejected:
            async with async_session_maker() as session:
                attempt = await _lock_attempt(session, attempt_id)
                if attempt is not None and attempt.status == "unknown":
                    await _cancel_attempt(session, attempt)
                    await _commit(session)
        logger.warning("[PayHOT autopay] Ответ списания требует сверки: {}", payment_id)
        return PipelineResult(ok=False, error=str(exc))
    async with async_session_maker() as session:
        attempt = await _lock_attempt(session, attempt_id)
        if attempt is None or attempt.status in {"applied", "canceled"}:
            return PipelineResult(ok=True, already_processed=True)
        try:
            attempt.provider_charge_id = _validate_charge(charge, attempt)
        except PaymentError:
            await _review(session, attempt, "Реквизиты ответа PayHOT не совпали с сохранённым запросом")
            await _commit(session)
            return PipelineResult(ok=False, error=texts.PAYHOT_AUTOPAY_PAYMENT_REVIEW_REQUIRED)
        if attempt.status not in {"succeeded", "manual_review"}:
            _set_status(session, attempt, "pending")
        await _commit(session)
    return await reconcile_payment(payment_id)


async def reconcile_payment(payment_id: str) -> PipelineResult:
    """Сверяет итог через GET API даже при выключенных автоплатежах."""
    async with async_session_maker() as session:
        attempt = await dal.get_attempt_by_payment_id(session, payment_id)
        if attempt is None:
            return PipelineResult(ok=False, error=texts.PAYHOT_INVOICE_NOT_FOUND)
        if attempt.status in {"applied", "canceled"}:
            return PipelineResult(ok=True, already_processed=True)
        attempt_id = attempt.id
        charge_id = attempt.provider_charge_id
    if not charge_id:
        return await _dispatch(attempt_id)
    try:
        response = await api_request("GET", f"/recurring-charges/{_identifier(charge_id)}")
    except PaymentError as exc:
        return PipelineResult(ok=False, error=str(exc))
    async with async_session_maker() as session:
        attempt = await _lock_attempt(session, attempt_id)
        if attempt is None or attempt.status in {"applied", "canceled"}:
            return PipelineResult(ok=True, already_processed=True)
        charge = response.get("recurring_charge")
        try:
            _validate_charge(charge, attempt)
        except PaymentError:
            await _review(session, attempt, "Ответ сверки PayHOT не соответствует попытке")
            await _commit(session)
            return PipelineResult(ok=False, error=texts.PAYHOT_AUTOPAY_PAYMENT_REVIEW_REQUIRED)
        if charge["status"] == "processing":
            if attempt.status not in {"succeeded", "manual_review"}:
                _set_status(session, attempt, "pending")
            await _commit(session)
            return PipelineResult(ok=True)
        if charge["status"] in {"failed", "cancelled"}:
            await _cancel_attempt(session, attempt)
            await _commit(session)
            return PipelineResult(ok=True)
        if attempt.user_id is None or attempt.owner_deleted:
            await _review(session, attempt, "Оплаченный автоплатёж принадлежит удалённому владельцу")
        else:
            _set_status(session, attempt, "succeeded" if not attempt.review_reason else "manual_review")
        await _commit(session)
        owner, charge_amount = attempt.user_id, float(Decimal(attempt.payload["amount_minor"]) / 100)
    if owner is None or attempt.owner_deleted:
        return PipelineResult(ok=False, error=texts.PAYHOT_AUTOPAY_OWNER_CHANGED)

    async def completion(session, owner, amount, row):
        """Передаёт оплаченный результат единственной попытке."""
        return await _complete_attempt(session, owner, amount, row, attempt_id=attempt_id)

    return await process_success_payment(
        PROVIDER,
        ParsedPayment(payment_id, UserId(owner), charge_amount, "RUB"),
        completion=completion,
        use_temporary_checkout=False,
    )


async def _prepare_subscription(subscription_id: int) -> str | None:
    """Резервирует баланс и сохраняет условия до отправки списания."""
    async with async_session_maker() as session:
        subscription = await dal.get_subscription(session, subscription_id, lock=True)
        if (
            subscription is None
            or not subscription.is_active
            or subscription.user_id is None
            or subscription.next_payment_date > datetime.utcnow()
            or not await is_payhot_autopay_enabled_for_session(session)
        ):
            return None
        existing = await dal.get_open_subscription_attempt(session, subscription_id)
        if existing is not None:
            return existing.id
        if subscription.retry_count >= int(PAYHOT_AUTOPAY_CONFIG.get("MAX_RETRY_ATTEMPTS", 3)):
            subscription.is_active = False
            await _commit(session)
            return None
        uid = UserId(subscription.user_id)
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
        cost, balance = money(quote["amount"]), money(user.balance or 0)
        reserved = min(balance, cost)
        charge = cost - reserved
        minimum = max(Decimal("0.01"), money(PAYHOT_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)))
        if 0 < charge < minimum:
            charge, reserved = min(cost, minimum), cost - min(cost, minimum)
        if charge:
            rub_amount(charge)
        attempt_id = str(uuid.uuid4())
        payment_id = f"payhot_recurring_{uuid.UUID(attempt_id).hex}"
        attempt = PayHotAutopayAttempt(
            id=attempt_id,
            user_id=int(uid),
            subscription_id=subscription.id,
            method_id=method.id,
            payment_id=payment_id,
            payload={
                "merchant_recurring_charge_reference": payment_id,
                "mandate_id": method.mandate_id,
                "amount_minor": str(int(charge * 100)),
                "currency": "RUB",
                "payment_method": method.payment_method,
            },
            intent={
                **quote,
                "original_user_id": int(uid),
                "cost": float(cost),
                "key_snapshot": checkout_key_snapshot(key),
                "reserved_balance": float(reserved),
                "balance_only": charge == 0,
            },
            status="prepared",
        )
        session.add(attempt)
        await session.flush()
        session.add(PayHotAutopayLog(attempt_id=attempt.id, status="prepared"))
        if reserved and await update_balance(session, uid, -float(reserved)) is None:
            raise ValueError(texts.PAYHOT_AUTOPAY_RESERVATION_FAILED)
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


async def run_once(bot=None, sessionmaker=async_session_maker) -> None:
    """Сверяет отправленные операции и затем запускает новые разрешённые."""
    async with sessionmaker() as session:
        attempts = await dal.list_unfinished_attempts(session)
        due = await dal.list_due_subscriptions(session) if await is_payhot_autopay_enabled_for_session(session) else []
        pending_bindings = list(
            (
                await session.scalars(
                    select(Payment)
                    .where(
                        Payment.payment_system == "payhot",
                        Payment.status == "success",
                        Payment.metadata_["payhot_enrollment_pending"].as_boolean().is_(True),
                    )
                    .order_by(Payment.id)
                    .limit(100)
                )
            ).all()
        )
        revoked = list(
            (
                await session.scalars(
                    select(PayHotSavedMethod).where(
                        PayHotSavedMethod.deleted_at.is_not(None), PayHotSavedMethod.revoked_at.is_(None)
                    )
                )
            ).all()
        )
    for attempt in attempts:
        try:
            await reconcile_payment(attempt.payment_id)
            async with sessionmaker() as session:
                current = await dal.lock_attempt(session, attempt.id)
                if current is not None and current.review_reason and not current.notice_sent:
                    delivered = await send_admin_alert(
                        f"⚠️ PayHOT: требуется сверка автоплатежа {current.payment_id}.\n{current.review_reason}"
                    )
                    if delivered:
                        current.notice_sent = True
                        await _commit(session)
        except Exception as exc:
            logger.error("[PayHOT autopay] Ошибка сверки {}: {}", attempt.payment_id, type(exc).__name__)
    from services.payments.payhot.service import reconcile_payment as reconcile_first

    for row in pending_bindings:
        if (row.metadata_ or {}).get("payhot_enrollment_pending"):
            try:
                await reconcile_first(row.payment_id)
            except PaymentError:
                logger.warning("[PayHOT autopay] Привязка ожидает сверки: {}", row.payment_id)
    for method in revoked:
        try:
            async with sessionmaker() as session:
                if method.user_id is not None:
                    await delete_method(session, UserId(method.user_id), method.id)
                else:
                    current = await session.get(PayHotSavedMethod, method.id)
                    if current is not None:
                        await _revoke_method(session, current)
                await _commit(session)
        except PaymentError:
            logger.warning("[PayHOT autopay] Отзыв разрешения ожидает сверки")
    for subscription in due:
        try:
            attempt_id = await _prepare_subscription(subscription.id)
            if attempt_id:
                await _dispatch(attempt_id)
        except Exception as exc:
            logger.error("[PayHOT autopay] Ошибка подготовки {}: {}", subscription.id, type(exc).__name__)


async def autopay_loop(bot, sessionmaker=async_session_maker) -> None:
    """Периодически сверяет и выполняет автопродление."""
    while True:
        try:
            await run_once(bot, sessionmaker)
        except Exception as exc:
            logger.error("[PayHOT autopay] Ошибка цикла: {}", type(exc).__name__)
        await asyncio.sleep(60)
