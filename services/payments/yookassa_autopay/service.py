from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from traceback import format_exc
from urllib.parse import urlparse

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot as default_bot
from core.settings.yookassa_autopay_config import (
    YOOKASSA_AUTOPAY_CONFIG,
    is_yookassa_autopay_enabled,
    is_yookassa_autopay_enabled_for_session,
    require_runtime_webhook_path,
)
from database import (
    async_session_maker,
    yookassa_autopay as dal,
)
from database.access.resolution import UserId, resolve_user_optional
from database.cache_purge import flush_purges
from database.models import User
from database.payments import add_payment, get_payment_by_payment_id, lock_payment_for_processing
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
    normalize_legacy_checkout_intent,
    prepare_checkout_completion,
)
from services.payments.pipeline import ParsedPayment, PipelineResult, process_cancelled_payment, process_success_payment
from services.payments.yookassa.amounts import YOOKASSA_AMOUNTS, quote_yookassa_amount, saved_yookassa_amounts
from settings import texts
from settings.buttons import YOOKASSA_AUTOPAY_SERVICE_CONFIRM_PAYMENT
from settings.config import REDIRECT_LINK, SERVICE_NAME

from .client import client, money


_IDEMPOTENCE_WINDOW = timedelta(hours=23, minutes=50)
_addons_notifier: Callable[..., Awaitable[None]] | None = None


def register_addons_notifier(notifier: Callable[..., Awaitable[None]]) -> None:
    """Подключает отправку карточки после докупки."""
    global _addons_notifier
    _addons_notifier = notifier


async def _commit(session: AsyncSession) -> None:
    await session.commit()
    await flush_purges(session)


def get_config() -> dict:
    return {
        **{key: value for key, value in YOOKASSA_AUTOPAY_CONFIG.items() if key not in {"SHOP_ID", "SECRET_KEY"}},
        "ENABLED": is_yookassa_autopay_enabled(),
    }


def _return_url(value: str | None) -> str:
    result = value or REDIRECT_LINK
    parsed = urlparse(result or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_RETURN_URL_REQUIRED)
    return result


async def _receipt(session: AsyncSession, user: User, amount: Decimal, description: str) -> dict | None:
    config = YOOKASSA_AUTOPAY_CONFIG
    if not config.get("SEND_RECEIPT", True):
        return None
    email = await dal.get_owner_email(session, UserId(user.id))
    email = email or config.get("RECEIPT_EMAIL")
    phone = config.get("RECEIPT_PHONE")
    customer = (
        {"email": str(email)}
        if email
        else ({"phone": str(phone)} if phone else {"email": f"{int(user.id)}@telegram.user"})
    )
    receipt = {
        "customer": customer,
        "items": [
            {
                "description": description[:128],
                "quantity": "1.00",
                "amount": {"value": str(amount), "currency": "RUB"},
                "vat_code": config.get("VAT_CODE", 1),
                "payment_mode": config.get("PAYMENT_MODE", "full_payment"),
                "payment_subject": config.get("PAYMENT_SUBJECT", "service"),
            }
        ],
    }
    if config.get("TAX_SYSTEM_CODE"):
        receipt["tax_system_code"] = int(config["TAX_SYSTEM_CODE"])
    return receipt


async def _payload(
    session: AsyncSession,
    user: User,
    amount: Decimal,
    attempt_id: str,
    *,
    card_id: str | None = None,
    return_url: str | None = None,
    save_method: bool = False,
) -> dict:
    description = " ".join(str(SERVICE_NAME or texts.YOOKASSA_AUTOPAY_SERVICE_SUBSCRIPTION_LABEL).split())[:128]
    require_runtime_webhook_path()
    payload = {
        "amount": {"value": str(amount), "currency": "RUB"},
        "capture": True,
        "description": description,
        "metadata": {"autopay_attempt_id": attempt_id, "user_id": str(user.id)},
    }
    if card_id:
        payload["payment_method_id"] = card_id
    else:
        payload["confirmation"] = {"type": "redirect", "return_url": _return_url(return_url)}
        if save_method:
            payload["save_payment_method"] = True
    receipt = await _receipt(session, user, amount, description)
    if receipt:
        payload["receipt"] = receipt
    return payload


async def quote_autopay_for_key(session: AsyncSession, user_id: UserId, client_id: str) -> dict:
    uid = UserId(user_id)
    key = await dal.get_owned_key(session, uid, client_id)
    if key is None or key.is_frozen:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_SUBSCRIPTION_UNAVAILABLE)
    tariff = await get_tariff_by_id(session, key.tariff_id)
    if not tariff:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_TARIFF_UNAVAILABLE)
    period = int(tariff.get("duration_days") or 0)
    if period <= 0:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_INVALID_TARIFF_PERIOD)
    terms = autopay_terms_for_tariff(tariff, key.selected_device_limit, key.selected_traffic_limit)
    if not terms:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_TARIFF_UNSUPPORTED)
    amount = money(terms["amount"])
    amount_quote = quote_yookassa_amount(amount)
    return {
        "amount": float(amount),
        "gross_amount": float(amount_quote.gross),
        "amount_quote": amount_quote.public("YOOKASSA_AUTOPAY"),
        "period_days": period,
        "label": tariff.get("name") or texts.YOOKASSA_AUTOPAY_SERVICE_SUBSCRIPTION_LABEL,
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
    card_id: str | None = None,
    *,
    consent: dict | bool | None = None,
):
    raw = session if isinstance(session, AsyncSession) else getattr(session, "_session", None)
    if raw is None:
        async with async_session_maker() as owned:
            subscription = await _enable_autopay_for_key(owned, user_id, client_id, card_id, consent=consent)
            await _commit(owned)
            return subscription
    return await _enable_autopay_for_key(raw, user_id, client_id, card_id, consent=consent)


async def _enable_autopay_for_key(
    session: AsyncSession,
    user_id: UserId,
    client_id: str,
    card_id: str | None = None,
    *,
    consent: dict | bool | None = None,
):
    if not await is_yookassa_autopay_enabled_for_session(session):
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_DISABLED)
    accepted = consent is True or isinstance(consent, dict) and consent.get("accepted") is True
    if not accepted:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_CONSENT_REQUIRED)
    uid = UserId(user_id)
    quote = await quote_autopay_for_key(session, uid, client_id)
    accepted_gross = consent.get("gross_amount") if isinstance(consent, dict) else None
    if accepted_gross is None and quote["gross_amount"] > quote["amount"]:
        raise ValueError(texts.YOOKASSA_MARKUP_CONSENT_REQUIRED)
    if accepted_gross is not None and money(quote["gross_amount"]) > money(accepted_gross):
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_PRICE_CHANGED)
    if isinstance(consent, dict) and consent.get("amount") is not None:
        if money(quote["amount"]) > money(consent["amount"]):
            raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_PRICE_CHANGED)
    if isinstance(consent, dict) and consent.get("period_days") is not None:
        if int(quote["period_days"]) != int(consent["period_days"]):
            raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_PERIOD_CHANGED)
    card = await dal.get_owned_card(session, uid, card_id) if card_id else await dal.get_active_card(session, uid)
    if card is None:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_SAVED_CARD_REQUIRED)
    next_date = datetime.utcfromtimestamp(quote["expiry_ms"] / 1000) - timedelta(
        days=int(YOOKASSA_AUTOPAY_CONFIG.get("DAYS_BEFORE_EXPIRY", 3))
    )
    return await dal.upsert_subscription(
        session,
        uid,
        client_id,
        card.id,
        quote["amount"],
        next_date,
        tariff_id=quote["tariff_id"],
        consent=accepted,
        accepted_gross_amount=quote["gross_amount"],
        period_days=quote["period_days"],
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
        raise ValueError("Для оплаты требуется внутренний user_id")
    if not await is_yookassa_autopay_enabled_for_session(session):
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_DISABLED)
    user = await dal.get_owner(session, user_id, lock=True)
    if user is None:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_OWNER_NOT_FOUND)
    value = money(amount)
    if value < Decimal(str(max(0.01, float(YOOKASSA_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1))))):
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_AMOUNT_BELOW_MINIMUM)
    metadata = copy.deepcopy(metadata or {})
    metadata.pop(YOOKASSA_AMOUNTS, None)
    quote = quote_yookassa_amount(value)
    expected_gross = metadata.pop("accepted_gross_amount", None)
    if expected_gross is not None and quote.gross != money(expected_gross):
        raise ValueError(texts.YOOKASSA_TERMS_CHANGED)
    fingerprint = hashlib.sha256(
        json.dumps(
            {"amount": str(value), "metadata": metadata, YOOKASSA_AMOUNTS: quote.metadata()},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    existing = await dal.find_open_checkout_attempt(session, user_id, fingerprint)
    if existing:
        existing_id = existing.id
        await _commit(session)
        return await _checkout_result(existing_id, success_url)
    checkout = await freeze_checkout_intent(session, user_id, metadata, float(value))
    attempt_id = str(uuid.uuid4())
    payload = await _payload(
        session,
        user,
        quote.gross,
        attempt_id,
        return_url=success_url,
        save_method=metadata.get("autopay_consent") is True,
    )
    cost = Decimal(str((checkout.get("data") or {}).get("cost") or 0))
    reserved = max(Decimal("0"), cost - value)
    if reserved and await update_balance(session, user_id, -float(reserved)) is None:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_BALANCE_CHANGED)
    intent = {
        "kind": "checkout",
        "checkout": checkout,
        "metadata": metadata,
        YOOKASSA_AMOUNTS: quote.metadata(),
        "checkout_fingerprint": fingerprint,
        "reserved_balance": float(reserved),
        "original_user_id": int(user_id),
        "autopay_consent": metadata.get("autopay_consent") is True,
    }
    attempt = await dal.prepare_attempt(
        session, user_id, payload=payload, intent=intent, attempt_id=attempt_id, idempotency_key=attempt_id
    )
    await _commit(session)
    attempt_id = attempt.id
    return await _checkout_result(attempt_id, success_url)


async def _checkout_result(attempt_id: str, success_url: str | None) -> tuple[str, str]:
    result = await _submit_attempt(attempt_id)
    async with async_session_maker() as read_session:
        saved = await dal.get_attempt(read_session, attempt_id)
        payment = await dal.get_legacy_payment(read_session, saved.payment_id) if saved and saved.payment_id else None
        if not payment:
            raise ValueError(result.error or texts.YOOKASSA_AUTOPAY_SERVICE_PAYMENT_PENDING)
        url = payment.confirmation_url
        if saved.status == "canceled":
            raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_PAYMENT_CANCELED)
        if not url:
            if saved.status == "applied":
                return _return_url(success_url), saved.payment_id
            raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_PAYMENT_PENDING)
        return url, saved.payment_id


create_checkout = create_checkout_payment


async def create_link(
    session: AsyncSession,
    tg_id: int,
    amount: float,
    currency: str,
    success_url: str | None,
    failure_url: str | None,
    metadata: dict | None,
) -> tuple[str, str]:
    if currency.upper() != "RUB":
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_RUB_REQUIRED)
    user = await resolve_user_optional(session, tg_id)
    if user is None:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_OWNER_NOT_FOUND)
    return await create_checkout_payment(
        session, UserId(user.id), amount, metadata=metadata, success_url=success_url, failure_url=failure_url
    )


def _verify_payment(attempt, data: dict) -> bool:
    try:
        metadata = data.get("metadata") or {}
        expected = attempt.payload["amount"]
        owned_metadata = (
            attempt.intent.get("legacy") is True
            or str(metadata.get("autopay_attempt_id")) == str(attempt.id)
            and str(metadata.get("user_id")) == str(attempt.payload["metadata"]["user_id"])
        )
        return (
            owned_metadata
            and money(data["amount"]["value"]) == money(expected["value"])
            and data["amount"]["currency"] == expected["currency"]
            and bool(data.get("id"))
            and (attempt.payment_id is None or attempt.payment_id == data["id"])
        )
    except (KeyError, TypeError, ValueError):
        return False


async def _save_provider_result(attempt_id: str, data: dict) -> PipelineResult:
    async with async_session_maker() as session:
        core_payment = await lock_payment_for_processing(session, str(data.get("id") or ""))
        attempt = await dal.lock_attempt(session, attempt_id)
        if attempt is None or not _verify_payment(attempt, data):
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_PAYMENT_MISMATCH)
        if attempt.user_id is None:
            await dal.update_attempt_result(session, attempt_id, status="manual_review", payment_id=data["id"])
            await _commit(session)
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_OWNER_DELETED_REVIEW)
        if attempt.status in {"applied", "canceled"}:
            return PipelineResult(ok=True, already_processed=True)
        status = str(data.get("status") or "")
        if status not in {"pending", "waiting_for_capture", "succeeded", "canceled"}:
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_UNKNOWN_STATUS)
        confirmation_url = (data.get("confirmation") or {}).get("confirmation_url")
        await dal.update_attempt_result(
            session,
            attempt_id,
            status="succeeded" if status == "succeeded" else "pending",
            payment_id=data["id"],
            confirmation_url=confirmation_url,
        )
        await dal.save_payment(
            session,
            UserId(attempt.user_id),
            payment_id=data["id"],
            amount=float(money(data["amount"]["value"])),
            status=status,
            payment_method_id=(data.get("payment_method") or {}).get("id"),
            confirmation_url=confirmation_url,
            is_autopay=attempt.subscription_id is not None,
            subscription_id=attempt.subscription_id,
            metadata=data.get("metadata"),
        )
        if core_payment is None:
            quote = saved_yookassa_amounts(attempt.intent)
            await add_payment(
                session,
                user_id=UserId(attempt.user_id),
                amount=float(quote.net if quote else money(data["amount"]["value"])),
                payment_system="YOOKASSA_AUTOPAY",
                payment_id=data["id"],
                status="pending",
                currency="RUB",
                metadata={"autopay_attempt_id": attempt_id, **({YOOKASSA_AMOUNTS: quote.metadata()} if quote else {})},
            )
        await _commit(session)
    return await reconcile_payment(data["id"])


async def _submit_attempt(attempt_id: str) -> PipelineResult:
    async with async_session_maker() as session:
        attempt = await dal.lock_attempt(session, attempt_id)
        if attempt is None:
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_ATTEMPT_NOT_FOUND)
        if attempt.status in {"applied", "canceled"}:
            return PipelineResult(ok=True, already_processed=True)
        if attempt.payment_id:
            payment_id = attempt.payment_id
            await session.rollback()
            return await reconcile_payment(payment_id)
        if attempt.user_id is None:
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_OWNER_DELETED_REVIEW)
        enabled = await is_yookassa_autopay_enabled_for_session(session)
        if (not enabled or attempt.consent_canceled) and attempt.status == "prepared":
            await _restore_reservation(session, attempt)
            await dal.update_attempt_result(session, attempt_id, status="canceled")
            await _commit(session)
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_NEW_CHARGES_DISABLED)
        if attempt.intent.get("balance_only"):
            await dal.update_attempt_result(session, attempt_id, status="succeeded")
            await _complete_attempt(session, UserId(attempt.user_id), 0, None, attempt_id=attempt_id, verified={})
            await _commit(session)
            return PipelineResult(ok=True)
        if attempt.status not in {"prepared", "unknown"}:
            return PipelineResult(ok=True, already_processed=True)
        if datetime.utcnow() - attempt.created_at >= _IDEMPOTENCE_WINDOW:
            await dal.update_attempt_result(session, attempt_id, status="manual_review")
            await _commit(session)
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_UNKNOWN_PAYMENT_REVIEW)
        if not enabled or attempt.consent_canceled:
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_NEW_CHARGES_DISABLED)
        payload = copy.deepcopy(attempt.payload)
        idempotency_key = attempt.idempotency_key
        await dal.update_attempt_result(session, attempt_id, status="unknown")
        await _commit(session)
    result = await client.create_payment(payload, idempotency_key)
    if result.data is not None:
        return await _save_provider_result(attempt_id, result.data)
    if not result.unknown:
        async with async_session_maker() as session:
            attempt = await dal.lock_attempt(session, attempt_id)
            if attempt and attempt.status not in {"applied", "succeeded", "canceled"}:
                await _restore_reservation(session, attempt)
                await dal.update_attempt_result(session, attempt_id, status="canceled")
                if attempt.subscription_id is not None:
                    await dal.fail_attempt_subscription(
                        session,
                        attempt.subscription_id,
                        datetime.utcnow()
                        + timedelta(hours=int(YOOKASSA_AUTOPAY_CONFIG.get("RETRY_INTERVAL_HOURS", 24))),
                    )
            await _commit(session)
    return PipelineResult(ok=False, error=result.error or texts.YOOKASSA_AUTOPAY_SERVICE_PAYMENT_UNKNOWN)


async def _complete_attempt(
    session: AsyncSession, owner: UserId, amount: float, payment_row, *, attempt_id: str, verified: dict
) -> None:
    attempt = await dal.lock_attempt(session, attempt_id)
    if attempt is None or int(attempt.user_id) != int(owner):
        raise ValueError("Владелец оплаты изменился")
    if attempt.status == "applied":
        return
    if attempt.status == "canceled":
        raise ValueError("Отмененный платеж не может быть исполнен")
    await dal.update_attempt_result(session, attempt_id, status="succeeded")
    await _restore_reservation(session, attempt)
    intent = attempt.intent or {}
    if intent.get("fulfillment_unavailable"):
        await _paid_review(session, owner, attempt, str(intent["fulfillment_unavailable"]))
        return
    checkout = intent.get("checkout") or {}
    checkout_data = checkout.get("data") or {}
    cost = Decimal(str(intent.get("cost") if intent.get("kind") == "recurring" else checkout_data.get("cost") or 0))
    current_owner = await dal.get_owner(session, owner, lock=True)
    if cost > 0 and Decimal(str(current_owner.balance or 0)) < cost:
        await _paid_review(session, owner, attempt, "Недостаточный остаток для оплаченной операции")
        return
    if intent.get("kind") != "recurring" and checkout.get("state") != "balance_topup":
        try:
            checkout = await prepare_checkout_completion(session, owner, checkout)
        except ServiceError as exc:
            await _paid_review(session, owner, attempt, str(exc))
            return
    method = verified.get("payment_method") or {}
    card_id = None
    if method.get("saved") is True and method.get("type") in {"bank_card", "sbp"} and method.get("id"):
        card = method.get("card") or {}
        card_id = str(method["id"])
        card_type = "SBP" if method.get("type") == "sbp" else card.get("card_type")
        card_mask = (
            texts.YOOKASSA_AUTOPAY_SERVICE_SBP_LABEL
            if method.get("type") == "sbp"
            else f"{card.get('first6', '')}…{card.get('last4', '')}"
        )
        try:
            saved_card = await dal.save_verified_card(
                session, owner, card_id, card_type=card_type, card_mask=card_mask, bank_name=card.get("issuer_name")
            )
        except ValueError as exc:
            await dal.append_log(session, owner, "payment_method_not_adopted", {"reason": str(exc)}, level="warning")
            card_id = None
        else:
            if saved_card.deleted_at is not None:
                card_id = None
    if intent.get("kind") == "recurring":
        key = await dal.get_owned_key(session, owner, intent["client_id"], lock=True)
        if key is None or key.is_frozen:
            await _paid_review(session, owner, attempt, "Оплаченный ключ удален или заморожен")
            return
        expected = intent.get("key_snapshot")
        unchanged = (
            checkout_key_snapshot(key) == expected
            if isinstance(expected, dict)
            else all((
                normalize_expiry_ms(key.expiry_time) == int(intent.get("expiry_ms") or 0),
                key.tariff_id == intent.get("tariff_id"),
                key.selected_device_limit == intent.get("selected_device_limit"),
                key.selected_traffic_limit == intent.get("selected_traffic_limit"),
                key.server_id == intent.get("server_id"),
            ))
        )
        if not unchanged:
            await _paid_review(session, owner, attempt, "Подписка изменилась после создания автоплатежа")
            return
        if not await get_tariff_by_id(session, int(intent["tariff_id"])):
            await _paid_review(session, owner, attempt, "Оплаченный тариф больше не существует")
            return
        final_expiry = max(normalize_expiry_ms(key.expiry_time), int(datetime.now(UTC).timestamp() * 1000)) + (
            int(intent["period_days"]) * 86400000
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
                    expected_key_snapshot=intent.get("key_snapshot") or checkout_key_snapshot(key),
                )
        except ServiceError as exc:
            await _paid_review(session, owner, attempt, str(exc))
            return
        next_date = datetime.utcfromtimestamp(final_expiry / 1000) - timedelta(
            days=int(YOOKASSA_AUTOPAY_CONFIG.get("DAYS_BEFORE_EXPIRY", 3))
        )
        await dal.mark_attempt_applied(session, attempt_id, next_payment_date=next_date)
        if attempt.subscription_id is None and intent.get("legacy_subscription_id") is not None:
            await dal.finish_subscription_cycle(session, int(intent["legacy_subscription_id"]), next_date)
    else:
        try:
            async with session.begin_nested():
                created_client = await apply_checkout_intent(session, owner, checkout, amount)
        except ServiceError as exc:
            await _paid_review(session, owner, attempt, str(exc))
            return
        metadata = intent.get("metadata") or {}
        flow = str(metadata.get("payment_flow") or (checkout.get("data") or {}).get("payment_flow") or "")
        if (
            card_id
            and not attempt.consent_canceled
            and intent.get("autopay_consent") is True
            and flow not in {"gift_create", "key_addons", "addons"}
        ):
            client_id = created_client or metadata.get("autopay_client_id")
            if client_id and await is_yookassa_autopay_enabled_for_session(session):
                approved = metadata.get("autopay_accepted_amount")
                period_days = metadata.get("autopay_accepted_period_days")
                approved_gross = metadata.get("autopay_accepted_gross_amount")
                if intent.get("legacy"):
                    approved = (
                        approved
                        or (checkout.get("data") or {}).get("autopay_amount")
                        or (checkout.get("data") or {}).get("selected_price_rub")
                    )
                    period_days = period_days or (checkout.get("data") or {}).get("selected_duration_days")
                if (approved is None or period_days is None) and not intent.get("legacy"):
                    await dal.append_log(session, owner, "paid_checkout_missing_recurring_terms", {})
                    await dal.mark_attempt_applied(session, attempt_id)
                    if attempt.payment_id:
                        await dal.mark_payment_status(session, attempt.payment_id, "succeeded")
                    return
                consent = {"accepted": True, "source": "paid_checkout"}
                if approved is not None:
                    consent["amount"] = approved
                if approved_gross is not None:
                    consent["gross_amount"] = approved_gross
                if period_days is not None:
                    consent["period_days"] = period_days
                try:
                    await enable_autopay_for_key(session, owner, client_id, card_id, consent=consent)
                except ValueError as exc:
                    await dal.append_log(session, owner, "paid_checkout_autopay_not_enabled", {"reason": str(exc)})
        await dal.mark_attempt_applied(session, attempt_id)
    if attempt.payment_id:
        await dal.mark_payment_status(session, attempt.payment_id, "succeeded")


async def _paid_review(session: AsyncSession, owner: UserId, attempt, reason: str) -> None:
    await dal.update_attempt_result(session, attempt.id, status="manual_review")
    sub_id = attempt.subscription_id or attempt.intent.get("legacy_subscription_id")
    if sub_id is not None:
        await dal.cancel_subscription(session, owner, int(sub_id))
    await dal.append_log(
        session, owner, "paid_operation_requires_review", {"attempt_id": attempt.id, "reason": reason}, level="warning"
    )
    if attempt.payment_id:
        await dal.mark_payment_status(session, attempt.payment_id, "succeeded")


async def _restore_reservation(session: AsyncSession, attempt) -> None:
    reserved = float((attempt.intent or {}).get("reserved_balance") or 0)
    if reserved and await dal.set_attempt_reservation_released(session, attempt.id):
        if await update_balance(session, UserId(attempt.user_id), reserved) is None:
            raise ValueError("Владелец резервного баланса отсутствует")


async def reconcile_payment(payment_id: str) -> PipelineResult:
    async with async_session_maker() as session:
        attempt = await dal.get_attempt_by_payment_id(session, payment_id)
        legacy = None if attempt else await dal.get_legacy_payment(session, payment_id)
    if attempt is None:
        if legacy is None:
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_PAYMENT_NOT_FOUND)
        return await _reconcile_legacy(legacy)
    if attempt.user_id is None:
        return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_OWNER_DELETED_REVIEW)
    if attempt.intent.get("legacy_unknown"):
        return PipelineResult(ok=True, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_UNKNOWN_REVIEW)
    result = await client.get_payment(payment_id)
    if result.data is None:
        return PipelineResult(ok=False, error=result.error)
    data = result.data
    if not _verify_payment(attempt, data):
        return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_SAVED_REQUEST_MISMATCH)
    parsed = ParsedPayment(
        payment_id,
        UserId(attempt.user_id),
        float(money(data["amount"]["value"])),
        currency="RUB",
        metadata={"autopay_attempt_id": attempt.id},
    )
    if data.get("status") == "succeeded" and data.get("paid") is True:

        async def completion(session, owner, amount, row):
            await _complete_attempt(session, owner, amount, row, attempt_id=attempt.id, verified=data)

        processed = await process_success_payment(
            "YOOKASSA_AUTOPAY", parsed, completion=completion, use_temporary_checkout=False
        )
        if processed.ok:
            await _emit_notices(attempt.id)
        return processed
    if data.get("status") == "canceled":
        processed = await process_cancelled_payment("YOOKASSA_AUTOPAY", parsed)
        if processed.ok:
            async with async_session_maker() as session:
                current = await dal.lock_attempt(session, attempt.id)
                if current and current.status not in {"applied", "canceled"}:
                    await _restore_reservation(session, current)
                    await dal.update_attempt_result(session, attempt.id, status="canceled")
                    if current.subscription_id is not None:
                        await dal.fail_attempt_subscription(
                            session,
                            current.subscription_id,
                            datetime.utcnow()
                            + timedelta(hours=int(YOOKASSA_AUTOPAY_CONFIG.get("RETRY_INTERVAL_HOURS", 24))),
                        )
                    await dal.mark_payment_status(session, payment_id, "canceled")
                    await _commit(session)
            await _emit_notices(attempt.id)
        return processed
    await _emit_notices(attempt.id)
    return PipelineResult(ok=True)


async def resolve_unknown_attempt(attempt_id: str, payment_id: str) -> PipelineResult:
    """Связывает неизвестную попытку с подтверждённым платежом ЮКассы."""
    async with async_session_maker() as session:
        attempt = await dal.get_attempt(session, attempt_id)
        if attempt and attempt.status in {"applied", "canceled"} and attempt.payment_id == payment_id:
            return PipelineResult(ok=True, already_processed=True)
        if attempt is None or attempt.status not in {"unknown", "manual_review", "pending"}:
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_UNFINISHED_ATTEMPT_NOT_FOUND)
    verified = await client.get_payment(payment_id)
    if not verified.data:
        return PipelineResult(ok=False, error=verified.error)
    if attempt.intent.get("legacy_unknown"):
        return await _resolve_legacy_unknown(attempt, verified.data)
    return await _save_provider_result(attempt_id, verified.data)


async def _resolve_legacy_unknown(marker, data: dict) -> PipelineResult:
    """Восстанавливает результат подтверждённого прежнего автоплатежа."""
    intent = marker.intent or {}
    metadata = data.get("metadata") or {}
    try:
        amount = money(data["amount"]["value"])
        balance_used = money(metadata.get("balance_to_use") or 0, positive=False)
        expected_method = str(intent["card_id"])
        actual_method = str((data.get("payment_method") or {}).get("id") or data.get("payment_method_id") or "")
        verified = (
            marker.user_id is not None
            and data["amount"]["currency"] == "RUB"
            and marker.payment_id in {None, data["id"]}
            and str(metadata.get("subscription_id")) == str(intent["legacy_subscription_id"])
            and str(metadata.get("client_id")) == str(intent["client_id"])
            and intent.get("legacy_tg_id") is not None
            and str(metadata.get("tg_id")) == str(intent["legacy_tg_id"])
            and actual_method == expected_method
            and amount + balance_used <= money(intent["legacy_amount"])
        )
    except (ValueError, KeyError, TypeError):
        verified = False
    if not verified:
        return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_VERIFICATION_FAILED)
    if data.get("status") != "succeeded" or data.get("paid") is not True:
        if data.get("status") == "canceled":
            async with async_session_maker() as session:
                prior = await lock_payment_for_processing(session, data["id"])
                if prior is not None and prior.user_id != int(marker.user_id):
                    return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_FOREIGN_OWNER)
            canceled = await process_cancelled_payment(
                "YOOKASSA_AUTOPAY", ParsedPayment(data["id"], UserId(marker.user_id), float(amount), "RUB")
            )
            if not canceled.ok:
                return canceled
            async with async_session_maker() as session:
                current = await dal.lock_attempt(session, marker.id)
                if current is None:
                    return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_ATTEMPT_NOT_FOUND)
                if current.status in {"applied", "canceled"}:
                    return PipelineResult(ok=True, already_processed=True)
                await dal.update_attempt_result(session, marker.id, status="canceled", payment_id=data["id"])
                await dal.fail_attempt_subscription(
                    session,
                    int(intent["legacy_subscription_id"]),
                    datetime.utcnow() + timedelta(hours=int(YOOKASSA_AUTOPAY_CONFIG.get("RETRY_INTERVAL_HOURS", 24))),
                )
                await dal.update_subscription_schedule(session, int(intent["legacy_subscription_id"]), is_active=False)
                await dal.mark_payment_status(session, data["id"], "canceled")
                await _commit(session)
            return PipelineResult(ok=True, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_CANCEL_CONFIRMED)
        return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_NOT_PAID)
    uid = UserId(marker.user_id)
    payment_id = data["id"]
    async with async_session_maker() as session:
        row = await lock_payment_for_processing(session, payment_id)
        if row is not None and (row.user_id != int(uid) or money(row.amount) != amount):
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_PAYMENT_MISMATCH)
        current = await dal.lock_attempt(session, marker.id)
        if current is None or current.user_id != int(uid):
            return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_OWNER_CHANGED_RETRY)
        if current.status == "applied":
            return PipelineResult(ok=True, already_processed=True)
        await dal.update_attempt_result(session, marker.id, status="manual_review", payment_id=payment_id)
        if row is None:
            await add_payment(
                session,
                user_id=uid,
                amount=float(amount),
                payment_system="YOOKASSA_AUTOPAY",
                payment_id=payment_id,
                status="pending",
                currency="RUB",
                metadata={"autopay_attempt_id": marker.id, "legacy_resolution": True},
            )
        await _commit(session)

    async def completion(session, owner, paid_amount, row):
        current = await dal.lock_attempt(session, marker.id)
        if current is None or current.user_id != int(owner):
            raise ValueError("Владелец прежней оплаты изменился")
        if current.status == "applied":
            return
        await dal.update_attempt_result(session, marker.id, status="succeeded")
        await dal.mark_attempt_applied(session, marker.id)
        await dal.update_subscription_schedule(session, int(intent["legacy_subscription_id"]), is_active=False)
        await dal.append_log(
            session,
            owner,
            "legacy_unknown_resolved_funds",
            {
                "payment_id": payment_id,
                "amount": float(amount),
                "balance_to_use": float(balance_used),
                "subscription_id": intent["legacy_subscription_id"],
                "renewal_requires_review": True,
            },
        )
        await notify_web(
            session,
            user_ref=owner,
            type="payment",
            title=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_CONFIRMED_TITLE,
            message=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_CONFIRMED_NOTICE,
        )

    result = await process_success_payment(
        "YOOKASSA_AUTOPAY",
        ParsedPayment(payment_id, uid, float(amount), "RUB"),
        completion=completion,
        use_temporary_checkout=False,
    )
    if result.ok:
        result.error = texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_CONFIRMED_RESULT
    return result


async def _emit_notices(attempt_id: str, bot=None) -> None:
    """Отправляет уведомления о попытке автоплатежа без повторов."""
    bot = bot or default_bot
    async with async_session_maker() as session:
        attempt = await dal.get_attempt(session, attempt_id)
        if attempt is None:
            return
        kind = None
        title = texts.YOOKASSA_AUTOPAY_SERVICE_NOTICE_TITLE
        message = ""
        confirmation = None
        config = YOOKASSA_AUTOPAY_CONFIG
        if attempt.status == "manual_review":
            payment = await get_payment_by_payment_id(session, attempt.payment_id) if attempt.payment_id else None
            if payment and payment.get("status") in {"success", "refunded", "chargebacked"}:
                kind = "paid_review"
                message = texts.YOOKASSA_AUTOPAY_SERVICE_PAID_REVIEW_NOTICE
            else:
                kind = "manual_review"
                message = texts.YOOKASSA_AUTOPAY_SERVICE_UNKNOWN_REVIEW_NOTICE
        elif attempt.status == "pending" and attempt.confirmation_url:
            kind = "confirmation"
            confirmation = attempt.confirmation_url
            if urlparse(confirmation).scheme != "https":
                confirmation = None
            message = texts.YOOKASSA_AUTOPAY_SERVICE_CONFIRM_PAYMENT_NOTICE
        elif attempt.status == "applied" and attempt.intent.get("balance_only") and config.get("NOTIFY_SUCCESS", True):
            kind = "balance_success"
            message = texts.YOOKASSA_AUTOPAY_SERVICE_BALANCE_SUCCESS_NOTICE
        elif (
            attempt.status == "applied"
            and config.get("NOTIFY_SUCCESS", True)
            and isinstance(attempt.intent.get("checkout"), dict)
            and attempt.intent["checkout"].get("state") == "waiting_for_addons_payment"
        ):
            kind = "addons_success"
            checkout_data = attempt.intent["checkout"].get("data") or {}
            message = texts.ADDONS_PACK_SUCCESS_TEXT if checkout_data.get("pack_mode") else texts.ADDONS_APPLIED_TEXT
        elif attempt.status == "canceled" and attempt.subscription_id is not None:
            sub = await dal.get_subscription(session, attempt.subscription_id)
            exhausted = sub and int(sub.retry_count or 0) >= int(config.get("MAX_RETRY_ATTEMPTS", 30))
            if exhausted and config.get("NOTIFY_ALL_ATTEMPTS_FAILED", True):
                kind = "all_failed"
                message = texts.YOOKASSA_AUTOPAY_SERVICE_ALL_ATTEMPTS_FAILED_NOTICE
            elif config.get("NOTIFY_FAILED_ATTEMPT", False):
                kind = "failed"
                message = texts.YOOKASSA_AUTOPAY_SERVICE_ATTEMPT_FAILED_NOTICE
        if kind is None or not await dal.claim_attempt_notice(session, attempt.id, kind):
            return
        user = await dal.get_owner(session, UserId(attempt.user_id)) if attempt.user_id is not None else None
        if user is not None:
            await notify_web(
                session,
                user_ref=UserId(user.id),
                type="payment",
                title=title,
                message=message,
                data={
                    "href": "/dashboard/subscriptions",
                    "payment_id": attempt.payment_id,
                    "confirmation_url": confirmation,
                },
            )
        chat_id = int(user.tg_id) if user and user.tg_id and int(user.tg_id) > 0 else None
        owner_id = UserId(user.id) if user is not None else None
        await _commit(session)
    if kind == "addons_success":
        if bot is not None and chat_id is not None and owner_id is not None and _addons_notifier is not None:
            try:
                async with async_session_maker() as session:
                    await _addons_notifier(bot, session, owner_id, str(checkout_data.get("client_id") or ""))
            except Exception as exc:
                logger.warning("[Autopay] Не удалось отправить карточку после докупки {}: {}", attempt_id, exc)
        return
    if kind in {"manual_review", "paid_review"}:
        details = (
            texts.YOOKASSA_AUTOPAY_SERVICE_PAID_REVIEW_DETAILS
            if kind == "paid_review"
            else texts.YOOKASSA_AUTOPAY_SERVICE_UNKNOWN_REVIEW_DETAILS
        )
        await send_admin_alert(
            texts.YOOKASSA_AUTOPAY_SERVICE_ADMIN_REVIEW_NOTICE.format(attempt_id=attempt_id, details=details)
        )
    if bot is not None and chat_id is not None:
        buttons = (
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=YOOKASSA_AUTOPAY_SERVICE_CONFIRM_PAYMENT, url=confirmation)]
                ]
            )
            if confirmation
            else None
        )
        try:
            await bot.send_message(chat_id, message, reply_markup=buttons)
        except Exception as exc:
            logger.warning("[Autopay] Не удалось отправить уведомление {}: {}", attempt_id, exc)


async def _reconcile_legacy(legacy) -> PipelineResult:
    """Сверяет прежний платёж с ЮКассой."""
    if legacy.user_id is None:
        return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_OWNER_REQUIRED)
    if legacy.status == "succeeded":
        return PipelineResult(ok=True, already_processed=True)
    result = await client.get_payment(legacy.payment_id)
    data = result.data
    if not data:
        return PipelineResult(ok=False, error=result.error)
    try:
        valid = (
            data["id"] == legacy.payment_id
            and data["amount"]["currency"] == "RUB"
            and money(data["amount"]["value"]) == money(legacy.amount)
        )
    except (ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        return PipelineResult(ok=False, error=texts.YOOKASSA_AUTOPAY_SERVICE_LEGACY_RECORD_MISMATCH)
    parsed = ParsedPayment(legacy.payment_id, UserId(legacy.user_id), float(legacy.amount), "RUB")
    if data.get("status") == "canceled":
        processed = await process_cancelled_payment("YOOKASSA_AUTOPAY", parsed)
        if processed.ok:
            async with async_session_maker() as session:
                await dal.get_owner(session, UserId(legacy.user_id), lock=True)
                current = await dal.get_legacy_payment(session, legacy.payment_id)
                if current and current.status not in {"succeeded", "canceled"}:
                    await dal.mark_payment_status(session, current.payment_id, "canceled")
                    if current.subscription_id is not None:
                        await dal.fail_attempt_subscription(
                            session,
                            current.subscription_id,
                            datetime.utcnow()
                            + timedelta(hours=int(YOOKASSA_AUTOPAY_CONFIG.get("RETRY_INTERVAL_HOURS", 24))),
                        )
                    await _commit(session)
        return processed
    if data.get("status") != "succeeded" or data.get("paid") is not True:
        return PipelineResult(ok=True)
    normalization_error = None
    try:
        metadata = json.loads(legacy.metadata_ or "{}")
        if not isinstance(metadata, dict):
            raise ValueError("Некорректные сохраненные условия прежней оплаты")
    except (TypeError, ValueError) as exc:
        metadata = {}
        normalization_error = str(exc)
    attempt_id = str(uuid.uuid4())
    async with async_session_maker() as session:
        await dal.get_owner(session, UserId(legacy.user_id), lock=True)
        existing = await dal.get_attempt_by_payment_id(session, legacy.payment_id)
        if existing is None:
            try:
                if normalization_error:
                    raise ValueError(normalization_error)
                if legacy.is_autopay and legacy.subscription_id is not None:
                    sub = await dal.get_subscription(session, legacy.subscription_id, lock=True)
                    if sub is None or sub.user_id != legacy.user_id:
                        raise ValueError("Прежняя подписка больше не принадлежит владельцу оплаты")
                    quote = await quote_autopay_for_key(session, UserId(legacy.user_id), sub.client_id)
                    balance_used = money(metadata.get("balance_to_use") or 0, positive=False)
                    cost = money(legacy.amount) + balance_used
                    target = (
                        max(quote["expiry_ms"], int(datetime.now(UTC).timestamp() * 1000))
                        + quote["period_days"] * 86400000
                    )
                    intent = {
                        "kind": "recurring",
                        "original_user_id": legacy.user_id,
                        "legacy": True,
                        **quote,
                        "legacy_subscription_id": sub.id,
                        "cost": float(cost),
                        "target_expiry_ms": target,
                        "reserved_balance": 0,
                    }
                else:
                    checkout = await normalize_legacy_checkout_intent(
                        session, UserId(legacy.user_id), metadata, float(legacy.amount)
                    )
                    intent = {
                        "kind": "checkout",
                        "checkout": checkout,
                        "metadata": metadata,
                        "original_user_id": legacy.user_id,
                        "legacy": True,
                        "autopay_consent": metadata.get("autopay_consent") in {True, "true"},
                    }
            except (ValueError, TypeError, KeyError, ServiceError) as exc:
                intent = {
                    "kind": "checkout",
                    "checkout": {"state": "balance_topup", "data": {}},
                    "original_user_id": legacy.user_id,
                    "legacy": True,
                    "metadata": metadata,
                    "legacy_subscription_id": legacy.subscription_id,
                    "reserved_balance": 0,
                    "fulfillment_unavailable": str(exc),
                }
            attempt = await dal.prepare_attempt(
                session,
                UserId(legacy.user_id),
                payload={
                    "amount": {"value": str(money(legacy.amount)), "currency": "RUB"},
                    "metadata": {"autopay_attempt_id": attempt_id, "user_id": str(legacy.user_id)},
                },
                intent=intent,
                attempt_id=attempt_id,
                idempotency_key=attempt_id,
            )
            await dal.update_attempt_result(session, attempt.id, status="succeeded", payment_id=legacy.payment_id)
            await _commit(session)
        else:
            attempt_id = existing.id

    async def completion(session, owner, amount, row):
        await _complete_attempt(session, owner, amount, row, attempt_id=attempt_id, verified=data)

    return await process_success_payment(
        "YOOKASSA_AUTOPAY", parsed, completion=completion, use_temporary_checkout=False
    )


async def process_verified_refund(payment_id: str, refund_id: str, amount: float) -> PipelineResult:
    confirmed = await reconcile_payment(payment_id)
    if not confirmed.ok:
        return confirmed
    try:
        async with async_session_maker() as session:
            attempt = await dal.get_attempt_by_payment_id(session, payment_id)
            legacy = None if attempt else await dal.get_legacy_payment(session, payment_id)
            owner = attempt.user_id if attempt else legacy.user_id if legacy else None
            if owner is None:
                raise ValueError("Владелец возвращаемой оплаты не найден")
            processed = await dal.apply_verified_refund(session, UserId(owner), payment_id, refund_id, amount)
            await _commit(session)
            return PipelineResult(ok=True, already_processed=not processed)
    except Exception as exc:
        logger.error("[Autopay] Не удалось обработать возврат {}: {}", refund_id, exc)
        return PipelineResult(ok=False, error=str(exc))


async def _prepare_subscription(subscription_id: int) -> str | None:
    async with async_session_maker() as session:
        sub = await dal.get_subscription(session, subscription_id)
        if not sub or not sub.is_active or sub.user_id is None:
            return None
        uid = UserId(sub.user_id)
        user = await dal.get_owner(session, uid, lock=True)
        if user is None or not await is_yookassa_autopay_enabled_for_session(session):
            return None
        sub = await dal.get_subscription(session, subscription_id, lock=True)
        if not sub.is_active or sub.next_payment_date > datetime.utcnow():
            return None
        existing = await dal.get_open_subscription_attempt(session, sub.id)
        if existing:
            return existing.id
        legacy_pending = await dal.get_legacy_pending(session, subscription_id=sub.id)
        if legacy_pending:
            return None
        if sub.last_payment_id:
            mirror = await dal.get_legacy_payment(session, sub.last_payment_id)
            prior_attempt = await dal.get_attempt_by_payment_id(session, sub.last_payment_id)
            core_payment = await get_payment_by_payment_id(session, sub.last_payment_id)
            terminal = (
                mirror
                and mirror.user_id == int(uid)
                and mirror.status in {"succeeded", "canceled"}
                or prior_attempt
                and prior_attempt.user_id == int(uid)
                and prior_attempt.status in {"applied", "canceled"}
                or core_payment
                and core_payment.get("user_id") == int(uid)
                and core_payment.get("status") in {"success", "cancelled", "failed", "refunded", "chargebacked"}
            )
            if terminal:
                await dal.finish_subscription_cycle(session, sub.id, sub.next_payment_date)
            else:
                await dal.update_subscription_schedule(session, sub.id, is_active=False)
                await dal.append_log(
                    session,
                    uid,
                    "legacy_payment_requires_review",
                    {"subscription_id": sub.id, "payment_id": sub.last_payment_id},
                    level="warning",
                )
                await _commit(session)
                return None
        if int(sub.retry_count or 0) >= int(YOOKASSA_AUTOPAY_CONFIG.get("MAX_RETRY_ATTEMPTS", 30)):
            await dal.update_subscription_schedule(session, sub.id, is_active=False)
            await _commit(session)
            return None
        try:
            key = await dal.get_owned_key(session, uid, sub.client_id, lock=True)
            quote = await quote_autopay_for_key(session, uid, sub.client_id)
        except ValueError as exc:
            await dal.update_subscription_schedule(session, sub.id, is_active=False)
            await dal.append_log(
                session,
                uid,
                "subscription_no_longer_renewable",
                {"client_id": sub.client_id, "reason": str(exc)},
                level="warning",
            )
            message = texts.YOOKASSA_AUTOPAY_SERVICE_SUBSCRIPTION_STOPPED_NOTICE
            await notify_web(
                session,
                user_ref=uid,
                type="payment",
                title=texts.YOOKASSA_AUTOPAY_SERVICE_NOTICE_TITLE,
                message=message,
                data={"href": "/dashboard/subscriptions", "client_id": sub.client_id},
            )
            chat_id = int(user.tg_id) if user.tg_id and int(user.tg_id) > 0 else None
            await _commit(session)
            if chat_id is not None:
                try:
                    await default_bot.send_message(chat_id, message)
                except Exception as notify_exc:
                    logger.warning("[Autopay] Не удалось отправить уведомление {}: {}", sub.id, notify_exc)
            return None
        if sub.period_days is not None and int(sub.period_days) != quote["period_days"]:
            await dal.update_subscription_schedule(session, sub.id, is_active=False)
            await dal.append_log(session, uid, "period_requires_consent", quote, level="warning")
            await _commit(session)
            return None
        if sub.period_days is None:
            await dal.update_subscription_schedule(session, sub.id, period_days=quote["period_days"])
        if money(quote["amount"]) > money(sub.amount):
            await dal.update_subscription_schedule(session, sub.id, is_active=False)
            await dal.append_log(session, uid, "price_requires_consent", quote, level="warning")
            await _commit(session)
            return None
        card = await dal.get_owned_card(session, uid, sub.card_id)
        if card is None:
            await dal.update_subscription_schedule(session, sub.id, is_active=False)
            await _commit(session)
            return None
        due = datetime.utcfromtimestamp(quote["expiry_ms"] / 1000) - timedelta(
            days=int(YOOKASSA_AUTOPAY_CONFIG.get("DAYS_BEFORE_EXPIRY", 3))
        )
        if due > datetime.utcnow():
            await dal.update_subscription_schedule(session, sub.id, next_payment_date=due)
            await _commit(session)
            return None
        attempt_id = str(uuid.uuid4())
        target = max(quote["expiry_ms"], int(datetime.now(UTC).timestamp() * 1000)) + quote["period_days"] * 86400000
        cost = money(quote["amount"])
        balance = Decimal(str(user.balance or 0)).quantize(Decimal("0.01"))
        if balance < 0:
            await dal.update_subscription_schedule(session, sub.id, is_active=False)
            await dal.append_log(
                session, uid, "negative_balance_requires_topup", {"client_id": sub.client_id}, level="warning"
            )
            await _commit(session)
            return None
        available = max(Decimal("0"), balance)
        reserved = min(available, cost).quantize(Decimal("0.01"))
        charge = cost - reserved
        min_charge = Decimal(str(max(0.01, float(YOOKASSA_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)))))
        if Decimal("0") < charge < min_charge:
            charge = min(cost, min_charge)
            reserved = cost - charge
        charge_quote = (
            quote_yookassa_amount(charge, percent=quote["amount_quote"]["fee_percent"]) if charge > 0 else None
        )
        cycle_total = reserved + (charge_quote.gross if charge_quote else charge)
        accepted_gross = sub.accepted_gross_amount if sub.accepted_gross_amount is not None else sub.amount
        if cycle_total > money(accepted_gross):
            await dal.update_subscription_schedule(session, sub.id, is_active=False)
            await dal.append_log(
                session,
                uid,
                "gross_price_requires_consent",
                {**quote, "cycle_total": str(cycle_total), "reserved_balance": str(reserved)},
                level="warning",
            )
            await _commit(session)
            return None
        intent = {
            "kind": "recurring",
            "original_user_id": int(uid),
            **quote,
            "cost": quote["amount"],
            "target_expiry_ms": target,
            "key_snapshot": checkout_key_snapshot(key),
            "reserved_balance": float(reserved),
            "balance_only": charge == 0,
        }
        if charge_quote:
            intent[YOOKASSA_AMOUNTS] = charge_quote.metadata()
        payload = await _payload(
            session, user, charge_quote.gross if charge_quote else charge, attempt_id, card_id=card.id
        )
        attempt = await dal.prepare_attempt(
            session,
            uid,
            payload=payload,
            intent=intent,
            attempt_id=attempt_id,
            idempotency_key=attempt_id,
            subscription_id=sub.id,
        )
        if attempt.id == attempt_id and reserved:
            if await update_balance(session, uid, -float(reserved)) is None:
                raise ValueError("Не удалось зарезервировать баланс подписки")
        await _commit(session)
        return attempt.id


async def run_once(bot=None, sessionmaker=async_session_maker) -> None:
    """Сверяет прошлые автоплатежи и обрабатывает очередные продления."""
    async with sessionmaker() as session:
        unfinished = await dal.list_unfinished_attempts(session)
        legacy = await dal.get_legacy_pending(session)
        due = await dal.list_due_subscriptions(session)
    known_ids = {attempt.payment_id for attempt in unfinished if attempt.payment_id}
    for attempt in unfinished:
        try:
            if attempt.intent.get("legacy_unknown"):
                await _emit_notices(attempt.id, bot)
                continue
            if attempt.payment_id:
                await reconcile_payment(attempt.payment_id)
            elif attempt.status in {"prepared", "unknown"}:
                await _submit_attempt(attempt.id)
            await _emit_notices(attempt.id, bot)
        except Exception as exc:
            logger.error("[Autopay] Не удалось сверить попытку {}: {}", attempt.id, exc)
    for payment in legacy:
        if payment.payment_id not in known_ids:
            try:
                await _reconcile_legacy(payment)
            except Exception as exc:
                logger.error("[Autopay] Не удалось сверить прежнюю оплату {}: {}", payment.payment_id, exc)
    for sub in due:
        try:
            attempt_id = await _prepare_subscription(sub.id)
            if attempt_id:
                await _submit_attempt(attempt_id)
                await _emit_notices(attempt_id, bot)
        except Exception as exc:
            logger.error("[Autopay] Не удалось обработать подписку {}: {}", sub.id, exc)


async def autopay_loop(bot, sessionmaker=async_session_maker) -> None:
    while True:
        try:
            await run_once(bot, sessionmaker)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "[Autopay] Ошибка фоновой проверки: {}: {!r}\n{}",
                type(exc).__name__,
                exc,
                format_exc(),
            )
        await asyncio.sleep(60)
