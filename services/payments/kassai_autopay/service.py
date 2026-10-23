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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.kassai_autopay_config import (
    KASSAI_AUTOPAY_CONFIG,
    is_kassai_autopay_enabled,
    is_kassai_autopay_enabled_for_session,
)
from database import (
    async_session_maker,
    kassai_autopay as dal,
)
from database.access.resolution import UserId, resolve_user_optional
from database.cache_purge import flush_purges
from database.models import KassaISavedMethod
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
    checkout_key_snapshot,
    freeze_checkout_intent,
    prepare_checkout_completion,
)
from services.payments.pipeline import ParsedPayment, PipelineResult, process_cancelled_payment, process_success_payment
from settings import texts
from settings.config import (
    KASSAI_API_KEY,
    KASSAI_DOMAIN,
    KASSAI_FAILURE_URL,
    KASSAI_IP,
    KASSAI_SECRET_KEY,
    KASSAI_SHOP_ID,
    KASSAI_SUCCESS_URL,
    REDIRECT_LINK,
)

from .client import client, money


PROVIDER = "KASSAI_AUTOPAY"


def require_payment_configuration() -> None:
    """Проверяет реквизиты до создания платёжного заказа."""
    fields = {
        "KASSAI_SHOP_ID": KASSAI_SHOP_ID,
        "KASSAI_API_KEY": KASSAI_API_KEY,
        "KASSAI_SECRET_KEY": KASSAI_SECRET_KEY,
        "KASSAI_IP": KASSAI_IP,
        "KASSAI_DOMAIN": KASSAI_DOMAIN,
    }
    missing = [name for name, value in fields.items() if not str(value or "").strip()]
    if missing:
        logger.error("KassaI: не заполнены настройки: {}", ", ".join(missing))
        raise ValueError(texts.KASSAI_AUTOPAY_UNAVAILABLE)
    try:
        ip_address(str(KASSAI_IP).strip())
    except ValueError as exc:
        logger.error("KassaI: в KASSAI_IP указан некорректный IP-адрес")
        raise ValueError(texts.KASSAI_AUTOPAY_UNAVAILABLE) from exc


async def _commit(session: AsyncSession) -> None:
    await session.commit()
    await flush_purges(session)


def get_config() -> dict:
    return {**KASSAI_AUTOPAY_CONFIG, "ENABLED": is_kassai_autopay_enabled()}


def _return_url(value: str | None) -> str:
    result = str(value or REDIRECT_LINK or "")
    parsed = urlparse(result)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Не задан адрес возврата после оплаты")
    return result


def _payload(user_id: int, amount: Decimal, payment_id: str, success_url=None, failure_url=None) -> dict:
    payload = {
        "i": 42,
        "email": f"{user_id}@{KASSAI_DOMAIN}",
        "ip": str(KASSAI_IP).strip(),
        "amount": str(amount),
        "currency": "RUB",
        "paymentId": payment_id,
    }
    for key, value in (
        ("success_url", success_url or KASSAI_SUCCESS_URL),
        ("failure_url", failure_url or KASSAI_FAILURE_URL),
    ):
        if value:
            payload[key] = _return_url(value)
    return payload


async def quote_autopay_for_key(session: AsyncSession, user_id: UserId, client_id: str) -> dict:
    key = await dal.get_owned_key(session, UserId(user_id), client_id)
    if key is None or key.is_frozen:
        raise ValueError("Подписка недоступна для автопродления")
    tariff = await get_tariff_by_id(session, key.tariff_id)
    if not tariff:
        raise ValueError("Тариф подписки не найден")
    terms = autopay_terms_for_tariff(
        tariff, key.selected_device_limit, key.selected_traffic_limit, provider_id=PROVIDER
    )
    if not terms or int(terms.get("period_days") or tariff.get("duration_days") or 0) <= 0:
        raise ValueError("Тариф недоступен для автопродления")
    amount = money(terms["amount"])
    minimum = max(Decimal("1"), money(KASSAI_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)))
    if amount < minimum:
        raise ValueError(f"Минимальная сумма автоплатежа — {minimum} ₽")
    return {
        "amount": float(amount),
        "period_days": int(terms.get("period_days") or tariff["duration_days"]),
        "label": tariff.get("name") or "Подписка",
        "tariff_id": int(key.tariff_id),
        "client_id": key.client_id,
        "key_email": key.email,
        "expiry_ms": normalize_expiry_ms(key.expiry_time),
        "selected_device_limit": key.selected_device_limit,
        "selected_traffic_limit": key.selected_traffic_limit,
        "server_id": key.server_id,
    }


async def enable_autopay_for_key(
    session: AsyncSession,
    user_id: UserId,
    client_id: str,
    method_id: str | None = None,
    *,
    consent: dict | None = None,
):
    if not await is_kassai_autopay_enabled_for_session(session):
        raise ValueError("Автоплатежи KassaI отключены")
    require_payment_configuration()
    if not isinstance(consent, dict) or consent.get("accepted") is not True:
        raise ValueError("Требуется согласие на регулярные списания")
    if consent.get("amount") is None or consent.get("period_days") is None:
        raise ValueError("Требуется согласие на сумму и период автоплатежа")
    await dal.get_owner(session, user_id, lock=True)
    quote = await quote_autopay_for_key(session, user_id, client_id)
    if money(quote["amount"]) > money(consent["amount"]):
        raise ValueError("Стоимость изменилась, подтвердите новые условия")
    if quote["period_days"] != int(consent["period_days"]):
        raise ValueError("Период изменился, подтвердите новые условия")
    method = (
        await dal.get_owned_method(session, user_id, method_id)
        if method_id
        else await dal.get_active_method(session, user_id)
    )
    if method is None:
        raise ValueError("Сначала привяжите счёт СБП")
    return await dal.upsert_subscription(
        session,
        user_id,
        client_id,
        method.id,
        amount=money(quote["amount"]),
        period_days=quote["period_days"],
        tariff_id=quote["tariff_id"],
        next_payment_date=datetime.utcfromtimestamp(quote["expiry_ms"] / 1000)
        - timedelta(days=int(KASSAI_AUTOPAY_CONFIG.get("DAYS_BEFORE_EXPIRY", 3))),
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
        raise ValueError("Для оплаты требуется внутренний идентификатор клиента")
    if not await is_kassai_autopay_enabled_for_session(session):
        raise ValueError("Автоплатежи KassaI отключены")
    require_payment_configuration()
    await dal.get_owner(session, user_id, lock=True)
    value = money(amount)
    minimum = max(Decimal("1"), money(KASSAI_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)))
    if value < minimum:
        raise ValueError(f"Минимальная сумма платежа — {minimum} ₽")
    metadata = {**copy.deepcopy(metadata or {}), "autopay_provider": PROVIDER}
    if metadata.get("autopay_consent") is not True:
        raise ValueError("Требуется согласие на сохранение счёта СБП")
    fingerprint = hashlib.sha256(
        json.dumps(
            {"amount": str(value), "metadata": metadata}, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()
    attempt = await dal.find_open_checkout_attempt(session, user_id, fingerprint)
    if attempt is None:
        checkout = await freeze_checkout_intent(session, user_id, metadata, float(value))
        attempt_id = str(uuid.uuid4())
        payment_id = f"kg_{uuid.UUID(attempt_id).hex}"
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
            raise ValueError("Баланс изменился, повторите оформление")
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
    raise ValueError(result.error or "Результат платежа проверяется. Повторное списание не отправлено")


async def cancel_subscription(session: AsyncSession, user_id: UserId, subscription_id: int) -> bool:
    result = await dal.cancel_subscription(session, user_id, subscription_id)
    await _commit(session)
    return result


async def create_link(session, user_ref, amount, currency, success_url, failure_url, metadata):
    """Создаёт ссылку оплаты для общего списка провайдеров."""
    if str(currency).upper() != "RUB":
        raise ValueError("KassaI поддерживает только рубли")
    user = await resolve_user_optional(session, user_ref)
    if user is None:
        raise ValueError("Клиент оплаты не найден")
    return await create_checkout_payment(
        session, UserId(user.id), amount, metadata=metadata, success_url=success_url, failure_url=failure_url
    )


async def delete_method(session: AsyncSession, user_id: UserId, method_id: str) -> bool:
    result = await dal.deactivate_method(session, user_id, method_id)
    await _commit(session)
    if result:
        await _revoke_method(method_id)
    return result


async def _revoke_method(method_id: str) -> None:
    async with async_session_maker() as session:
        method = await session.get(KassaISavedMethod, method_id)
        if method is None or not method.revoke_pending:
            return
        token = method.token
    result = await client.delete_method(token)
    if result.data is not None:
        async with async_session_maker() as session:
            method = await session.get(KassaISavedMethod, method_id)
            if method is not None:
                method.revoke_pending = False
                await _commit(session)


async def _restore_reservation(session: AsyncSession, attempt) -> None:
    if attempt.reservation_released:
        return
    reserved = float((attempt.intent or {}).get("reserved_balance") or 0)
    if reserved:
        if attempt.user_id is None or await update_balance(session, UserId(attempt.user_id), reserved) is None:
            raise ValueError("Владелец резервного баланса отсутствует")
    attempt.reservation_released = True
    await session.flush()


async def _cancel_attempt(session: AsyncSession, attempt) -> None:
    await _restore_reservation(session, attempt)
    attempt.status = "canceled"
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
                hours=int(KASSAI_AUTOPAY_CONFIG.get("RETRY_INTERVAL_HOURS", 24))
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
            return PipelineResult(ok=False, error="Попытка платежа не найдена")
        if attempt.status in {"applied", "canceled"}:
            return PipelineResult(ok=True, already_processed=True)
        if attempt.status != "prepared":
            payment_id = attempt.payment_id
            await session.rollback()
            return await reconcile_payment(payment_id)
        if attempt.user_id is None or attempt.owner_deleted:
            attempt.status, attempt.review_reason = "manual_review", "Владелец платежа удалён"
            await _commit(session)
            return PipelineResult(ok=False, error="Требуется проверка платежа")
        if attempt.consent_canceled or not await is_kassai_autopay_enabled_for_session(session):
            await _cancel_attempt(session, attempt)
            await _commit(session)
            return PipelineResult(ok=False, error="Новые списания отключены")
        if attempt.intent.get("balance_only"):
            attempt.status = "succeeded"
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
                return PipelineResult(ok=False, error="Согласие на списание отменено")
            payload["us_kgt"] = method.token
        attempt.status = "unknown"
        await _commit(session)

    async def before_send():
        return await _authorize_dispatch(attempt_id)

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
            order_id = result.data.get("orderId")
            if not order_id:
                return PipelineResult(ok=False, error="KassaI не подтвердила номер заказа")
            if attempt.provider_order_id is not None and attempt.provider_order_id != str(order_id):
                return PipelineResult(ok=False, error="Номер заказа KassaI не совпадает")
            attempt.provider_order_id = str(order_id)
            location = result.data.get("location")
            if isinstance(location, str) and location:
                attempt.confirmation_url = _return_url(location)
            attempt.status = "pending"
        elif not result.unknown:
            await _cancel_attempt(session, attempt)
        await _commit(session)
    return PipelineResult(ok=result.data is not None, error=result.error)


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
        allowed = not attempt.consent_canceled and await is_kassai_autopay_enabled_for_session(session)
        if attempt.subscription_id is not None:
            subscription = await dal.get_subscription(session, attempt.subscription_id, lock=True)
            method = await dal.get_owned_method(session, attempt.user_id, attempt.method_id)
            allowed = allowed and subscription is not None and subscription.is_active and method is not None
        minimum = max(Decimal("1"), money(KASSAI_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)))
        allowed = allowed and money(attempt.payload["amount"]) >= minimum
        if not allowed:
            await _cancel_attempt(session, attempt)
            await _commit(session)
            return False
        attempt.dispatched_at = datetime.utcnow()
        await _commit(session)
        return True


def _matches_attempt(attempt, data: dict, *, webhook: bool) -> bool:
    try:
        if webhook:
            merchant_matches = str(data.get("MERCHANT_ID") or "") == str(KASSAI_SHOP_ID)
            payment_id, amount, order_id = data["MERCHANT_ORDER_ID"], data["AMOUNT"], data.get("intid")
        else:
            merchant_matches = str(data.get("currency", "")).upper() == "RUB"
            payment_id, amount, order_id = data["merchant_order_id"], data["amount"], data.get("fk_order_id")
        return (
            merchant_matches
            and payment_id == attempt.payment_id
            and money(amount) == money(attempt.payload["amount"])
            and bool(order_id)
            and (attempt.provider_order_id is None or str(order_id) == attempt.provider_order_id)
        )
    except (KeyError, ValueError, TypeError):
        return False


async def process_recurrent_init(data: dict) -> PipelineResult:
    payment_id = str(data.get("MERCHANT_ORDER_ID") or "")
    async with async_session_maker() as session:
        found = await dal.get_attempt_by_payment_id(session, payment_id)
        if found is None:
            return PipelineResult(ok=False, error="Заказ привязки не найден")
        attempt = await dal.lock_attempt(session, found.id)
        if (
            not _matches_attempt(attempt, data, webhook=True)
            or attempt.subscription_id is not None
            or not attempt.intent.get("autopay_consent")
            or attempt.user_id is None
            or attempt.owner_deleted
        ):
            return PipelineResult(ok=False, error="Привязка не соответствует заказу")
        method = await dal.save_verified_method(session, attempt.user_id, data.get("TOKEN"), payment_id)
        if attempt.method_id is not None and attempt.method_id != method.id:
            return PipelineResult(ok=False, error="У заказа уже есть другая привязка")
        attempt.method_id = method.id
        if attempt.consent_canceled or method.deleted_at is not None:
            method.is_active = False
            if method.deleted_at is None:
                method.deleted_at, method.revoke_pending = datetime.utcnow(), True
        elif attempt.status == "applied":
            await _adopt_checkout_consent(session, attempt)
        await _commit(session)
    return PipelineResult(ok=True)


async def process_payment_webhook(data: dict) -> PipelineResult | None:
    payment_id = str(data.get("MERCHANT_ORDER_ID") or "")
    async with async_session_maker() as session:
        attempt = await dal.get_attempt_by_payment_id(session, payment_id)
        if attempt is None:
            if payment_id.startswith("kg_"):
                return PipelineResult(ok=False, error="Заказ автоплатежа не найден")
            return None
        if not _matches_attempt(attempt, data, webhook=True):
            return PipelineResult(ok=False, error="Оплата не соответствует сохранённому заказу")
    return await _process_confirmed(attempt)


async def _process_confirmed(attempt) -> PipelineResult:
    if attempt.user_id is None or attempt.owner_deleted:
        return PipelineResult(ok=False, error="Владелец оплаченного заказа удалён")
    parsed = ParsedPayment(attempt.payment_id, UserId(attempt.user_id), float(money(attempt.payload["amount"])), "RUB")

    async def completion(session, owner, amount, row):
        await _complete_attempt(session, owner, amount, row, attempt_id=attempt.id)

    return await process_success_payment(PROVIDER, parsed, completion=completion, use_temporary_checkout=False)


async def reconcile_payment(payment_id: str) -> PipelineResult:
    async with async_session_maker() as session:
        attempt = await dal.get_attempt_by_payment_id(session, payment_id)
    if attempt is None:
        return PipelineResult(ok=False, error="Заказ автоплатежа не найден")
    if attempt.status in {"applied", "canceled"}:
        return PipelineResult(ok=True, already_processed=True)
    if attempt.status == "prepared":
        return await _submit_attempt(attempt.id)
    if attempt.review_reason:
        return PipelineResult(ok=False, error="Требуется ручная проверка платежа")
    result = await client.get_payment(payment_id)
    if result.data is None:
        return PipelineResult(ok=False, error=result.error)
    if not _matches_attempt(attempt, result.data, webhook=False):
        return PipelineResult(ok=False, error="KassaI вернула другой заказ или сумму")
    try:
        status = int(result.data["status"])
    except (KeyError, ValueError, TypeError):
        return PipelineResult(ok=False, error="KassaI не подтвердила статус заказа")
    if status == 1:
        return await _process_confirmed(attempt)
    if status in {8, 9}:
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
    if status == 6:
        parsed = ParsedPayment(
            payment_id,
            UserId(attempt.user_id) if attempt.user_id else None,
            float(money(attempt.payload["amount"])),
            "RUB",
        )
        processed = await process_cancelled_payment(PROVIDER, parsed, new_status="refunded")
        if not processed.ok:
            return processed
        async with async_session_maker() as session:
            current = await _lock_attempt_for_invoice(session, attempt.id)
            await _restore_reservation(session, current)
            await _paid_review(session, current, "Заказ возвращён, требуется сверка возврата")
            current.payment_finalized = True
            await _commit(session)
        return PipelineResult(ok=False, error="Возврат требует ручной проверки")
    return PipelineResult(ok=status == 0)


async def _paid_review(session: AsyncSession, attempt, reason: str) -> None:
    attempt.status, attempt.review_reason = "manual_review", reason
    if attempt.subscription_id is not None:
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
        and await is_kassai_autopay_enabled_for_session(session)
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
        logger.info("KassaI: условия автопродления требуют повторного согласия, attempt={}", attempt.id)


async def _complete_attempt(session, owner, amount, payment_row, *, attempt_id: str) -> None:
    attempt = await dal.lock_attempt(session, attempt_id)
    if attempt is None or attempt.user_id != int(owner) or attempt.owner_deleted:
        raise ValueError("Владелец заказа изменился")
    if attempt.status == "applied" or attempt.review_reason:
        return
    if attempt.status == "canceled":
        raise ValueError("Отменённый заказ нельзя исполнить")
    attempt.status = "succeeded"
    await _restore_reservation(session, attempt)
    intent = attempt.intent or {}
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
        subscription = await dal.get_subscription(session, attempt.subscription_id, lock=True)
        if subscription is not None:
            subscription.next_payment_date = datetime.utcfromtimestamp(final_expiry / 1000) - timedelta(
                days=int(KASSAI_AUTOPAY_CONFIG.get("DAYS_BEFORE_EXPIRY", 3))
            )
            subscription.retry_count = 0
    else:
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
    attempt.status = "applied"
    attempt.payment_finalized = True
    await session.flush()


async def _prepare_subscription(subscription_id: int) -> str | None:
    async with async_session_maker() as session:
        subscription = await dal.get_subscription(session, subscription_id, lock=True)
        if (
            subscription is None
            or subscription.user_id is None
            or not subscription.is_active
            or subscription.next_payment_date > datetime.utcnow()
            or not await is_kassai_autopay_enabled_for_session(session)
        ):
            return None
        uid = UserId(subscription.user_id)
        existing = await dal.get_open_subscription_attempt(session, subscription_id)
        if existing:
            return existing.id
        if subscription.retry_count >= int(KASSAI_AUTOPAY_CONFIG.get("MAX_RETRY_ATTEMPTS", 30)):
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
        due = datetime.utcfromtimestamp(quote["expiry_ms"] / 1000) - timedelta(
            days=int(KASSAI_AUTOPAY_CONFIG.get("DAYS_BEFORE_EXPIRY", 3))
        )
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
        minimum = max(Decimal("1"), money(KASSAI_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)))
        if Decimal("0") < charge < minimum:
            charge = min(cost, minimum)
            reserved = cost - charge
        attempt_id = str(uuid.uuid4())
        payment_id = f"kg_{uuid.UUID(attempt_id).hex}"
        intent = {
            "kind": "recurring",
            "original_user_id": int(uid),
            **quote,
            "cost": float(cost),
            "key_snapshot": checkout_key_snapshot(key),
            "reserved_balance": float(reserved),
            "balance_only": charge == 0,
        }
        attempt = await dal.prepare_attempt(
            session,
            uid,
            attempt_id=attempt_id,
            payment_id=payment_id,
            subscription_id=subscription.id,
            method_id=method.id,
            payload=_payload(uid, charge, payment_id),
            intent=intent,
        )
        if reserved and await update_balance(session, uid, -float(reserved)) is None:
            raise ValueError("Не удалось зарезервировать баланс подписки")
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
                title="Автоплатёж KassaI",
                message="Платёж требует проверки. Подтверждённые средства сохранены на балансе.",
                data={"href": "/dashboard/subscriptions", "payment_id": attempt.payment_id},
            )
        await _commit(session)
    await send_admin_alert(f"KassaI: требуется проверка автоплатежа {attempt_id}. Новое списание заблокировано.")


async def run_once(bot=None, sessionmaker=async_session_maker) -> None:
    """Сверяет отправленные платежи и запускает согласованные продления."""
    async with sessionmaker() as session:
        attempts = await dal.list_unfinished_attempts(session)
        cancellations = await dal.list_unfinalized_cancellations(session)
        due = await dal.list_due_subscriptions(session)
        revoke = list(
            (
                await session.scalars(select(KassaISavedMethod.id).where(KassaISavedMethod.revoke_pending.is_(True)))
            ).all()
        )
    for attempt in attempts:
        try:
            if attempt.status == "prepared":
                await _submit_attempt(attempt.id)
            else:
                await reconcile_payment(attempt.payment_id)
            await _notify_review(attempt.id)
        except Exception:
            logger.error("KassaI: не удалось сверить попытку {}", attempt.id)
    for subscription in due:
        try:
            attempt_id = await _prepare_subscription(subscription.id)
            if attempt_id:
                await _submit_attempt(attempt_id)
                await _notify_review(attempt_id)
        except Exception:
            logger.error("KassaI: не удалось обработать подписку {}", subscription.id)
    for method_id in revoke:
        try:
            await _revoke_method(method_id)
        except Exception:
            logger.error("KassaI: не удалось отозвать привязку {}", method_id)
    for attempt in cancellations:
        try:
            await _finalize_cancellation(attempt)
        except Exception:
            logger.error("KassaI: не удалось завершить отмену {}", attempt.id)


async def autopay_loop(bot, sessionmaker=async_session_maker) -> None:
    while True:
        try:
            await run_once(bot, sessionmaker)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("KassaI: ошибка фоновой проверки автоплатежей")
        await asyncio.sleep(60)
