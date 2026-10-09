import asyncio
import json

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_token
from api.shared.http import resolve_default_web_payment_provider
from api.v2.schemas.payment_links import (
    PaymentAmountQuote,
    PaymentLinkCreateRequest,
    PaymentLinkCreateResponse,
    PaymentLinkStatusResponse,
)
from core.settings.payments_config import PAYMENTS_CONFIG
from database import (
    async_session_maker,
    get_payment_by_payment_id,
    get_payment_from_db_by_payment_id,
    identities as idb,
)
from database.access.resolution import TelegramId, resolve_user_optional
from database.tariffs import get_tariff_by_id
from database.temporary_data import create_temporary_data
from logger import logger
from services.errors import ServiceError
from services.payments.checkout_intent import freeze_checkout_intent
from services.payments.payment_events import payment_events_channel
from services.payments.payment_links import PaymentLinkRequest, create_payment_link, quote_payment_amount
from services.payments.yookassa.amounts import YOOKASSA_PROVIDERS
from services.tariffs.cooldown import get_tariff_cooldown_remaining
from services.tariffs.visibility import is_tariff_visible_for
from settings import texts
from settings.config import REDIS_URL


_PAYMENT_LINK_TTL_MIN = 60


def _payment_link_expired(created_at) -> bool:
    if created_at is None:
        return False
    try:
        tz = getattr(created_at, "tzinfo", None)
        now = datetime.now(tz) if tz is not None else datetime.now()
        age = (now - created_at).total_seconds()
        return age > _PAYMENT_LINK_TTL_MIN * 60
    except Exception:
        return False


router = APIRouter(tags=["PaymentLinks"])


@router.post("/quote", response_model=PaymentAmountQuote)
async def quote_link(body: PaymentLinkCreateRequest, identity=Depends(verify_identity_token)):
    """Показывает итоговую сумму без создания платежа."""
    provider = body.provider_id or resolve_default_web_payment_provider() or ""
    provider = provider.strip().upper()
    enabled_id = "YOOKASSA_AUTOPAY" if provider == "YOOKASSA_AUTOPAY_WEB" else provider
    if provider in YOOKASSA_PROVIDERS and not PAYMENTS_CONFIG.get(enabled_id):
        raise HTTPException(status_code=400, detail=texts.YOOKASSA_PROVIDER_DISABLED)
    try:
        return quote_payment_amount(provider, body.amount, body.currency)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _validate_payment_intent(
    session: AsyncSession,
    billing_user_ref: int,
    metadata: dict | None,
) -> None:
    if not isinstance(metadata, dict):
        return
    if str(metadata.get("payment_flow") or "").strip().lower() != "tariff_purchase":
        return
    tariff_id = metadata.get("tariff_id")
    if tariff_id in (None, ""):
        return
    try:
        tariff_id_int = int(tariff_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный тариф")
    tariff = await get_tariff_by_id(session, tariff_id_int)
    if not tariff or not await is_tariff_visible_for(session, billing_user_ref, tariff):
        raise HTTPException(status_code=404, detail="Тариф недоступен")
    cooldown_left = await get_tariff_cooldown_remaining(
        session, billing_user_ref, tariff_id_int, int(tariff.get("cooldown_days") or 0)
    )
    if cooldown_left > 0:
        raise HTTPException(status_code=429, detail="Тариф временно недоступен для повторной покупки")


async def _store_payment_intent(
    session: AsyncSession,
    billing_user_ref: int,
    metadata: dict | None,
    amount: int | float,
) -> None:
    if not isinstance(metadata, dict):
        return
    payment_flow = str(metadata.get("payment_flow") or "").strip().lower()
    if payment_flow not in {"tariff_purchase", "key_renewal", "key_addons"}:
        return
    intent = await freeze_checkout_intent(session, int(billing_user_ref), metadata, float(amount))
    metadata = intent["data"]
    required_amount = int(round(float(amount)))
    if payment_flow == "tariff_purchase":
        tariff_id = metadata.get("tariff_id")
        if tariff_id in (None, ""):
            return
        payload: dict[str, int | str] = {
            "tariff_id": int(tariff_id),
            "required_amount": required_amount,
            "selected_price_rub": int(metadata["selected_price_rub"])
            if metadata.get("selected_price_rub") is not None
            else required_amount,
        }
        selected_device_limit = metadata.get("selected_device_limit")
        if selected_device_limit not in (None, ""):
            payload["selected_device_limit"] = int(selected_device_limit)
        selected_traffic_gb = metadata.get("selected_traffic_limit_gb")
        if selected_traffic_gb not in (None, ""):
            payload["selected_traffic_limit_gb"] = int(selected_traffic_gb)
        selected_duration_days = metadata.get("selected_duration_days")
        if selected_duration_days not in (None, ""):
            payload["selected_duration_days"] = int(selected_duration_days)
        coupon_id = metadata.get("coupon_id")
        if coupon_id not in (None, ""):
            payload["coupon_id"] = int(coupon_id)
        discount_rub = metadata.get("discount_rub")
        if discount_rub not in (None, ""):
            payload["discount_rub"] = int(discount_rub)
        base_price_rub = metadata.get("base_price_rub")
        if base_price_rub not in (None, ""):
            payload["base_price_rub"] = int(base_price_rub)
        applied_coupon_code = metadata.get("applied_coupon_code")
        if applied_coupon_code not in (None, ""):
            payload["applied_coupon_code"] = str(applied_coupon_code)
        await create_temporary_data(session, billing_user_ref, "waiting_for_payment", payload)
        return
    if payment_flow == "key_renewal":
        required_fields = ("tariff_id", "client_id", "email", "cost")
        if any(metadata.get(field) in (None, "") for field in required_fields):
            return
        payload: dict[str, int | str] = {
            "tariff_id": int(metadata["tariff_id"]),
            "client_id": str(metadata["client_id"]),
            "email": str(metadata["email"]),
            "cost": int(metadata["cost"]),
            "required_amount": required_amount,
            "selected_price_rub": int(metadata["selected_price_rub"])
            if metadata.get("selected_price_rub") is not None
            else int(metadata["cost"]),
        }
        selected_duration_days = metadata.get("renewal_duration_days")
        if selected_duration_days not in (None, ""):
            payload["selected_duration_days"] = int(selected_duration_days)
        selected_device_limit = metadata.get("selected_device_limit")
        if selected_device_limit not in (None, ""):
            payload["selected_device_limit"] = int(selected_device_limit)
        selected_traffic_limit = metadata.get("selected_traffic_limit")
        if selected_traffic_limit not in (None, ""):
            payload["selected_traffic_limit"] = int(selected_traffic_limit)
        total_gb = metadata.get("total_gb")
        if total_gb not in (None, ""):
            payload["total_gb"] = int(total_gb)
        coupon_id = metadata.get("coupon_id")
        if coupon_id not in (None, ""):
            payload["coupon_id"] = int(coupon_id)
        discount_rub = metadata.get("discount_rub")
        if discount_rub not in (None, ""):
            payload["discount_rub"] = int(discount_rub)
        base_price_rub = metadata.get("base_price_rub")
        if base_price_rub not in (None, ""):
            payload["base_price_rub"] = int(base_price_rub)
        applied_coupon_code = metadata.get("applied_coupon_code")
        if applied_coupon_code not in (None, ""):
            payload["applied_coupon_code"] = str(applied_coupon_code)
        await create_temporary_data(session, billing_user_ref, "waiting_for_renewal_payment", payload)
        return
    if payment_flow == "key_addons":
        required_fields = ("tariff_id", "email", "original_price")
        if any(metadata.get(field) in (None, "") for field in required_fields):
            return
        payload: dict[str, int | str] = {
            "tariff_id": int(metadata["tariff_id"]),
            "email": str(metadata["email"]),
            "original_price": int(metadata["original_price"]),
            "agreed_extra_price": int(metadata["cost"]),
            "required_amount": required_amount,
        }
        selected_device_limit = metadata.get("selected_device_limit")
        if selected_device_limit not in (None, ""):
            payload["selected_device_limit"] = int(selected_device_limit)
        selected_traffic_gb = metadata.get("selected_traffic_limit_gb")
        if selected_traffic_gb not in (None, ""):
            payload["selected_traffic_gb"] = int(selected_traffic_gb)
        current_device_limit = metadata.get("current_device_limit")
        if current_device_limit not in (None, ""):
            payload["current_device_limit"] = int(current_device_limit)
        current_traffic_gb = metadata.get("current_traffic_gb")
        if current_traffic_gb not in (None, ""):
            payload["current_traffic_gb"] = int(current_traffic_gb)
        coupon_id = metadata.get("coupon_id")
        if coupon_id not in (None, ""):
            payload["coupon_id"] = int(coupon_id)
        discount_rub = metadata.get("discount_rub")
        if discount_rub not in (None, ""):
            payload["discount_rub"] = int(discount_rub)
        base_price_rub = metadata.get("base_price_rub")
        if base_price_rub not in (None, ""):
            payload["base_price_rub"] = int(base_price_rub)
        applied_coupon_code = metadata.get("applied_coupon_code")
        if applied_coupon_code not in (None, ""):
            payload["applied_coupon_code"] = str(applied_coupon_code)
        await create_temporary_data(session, billing_user_ref, "waiting_for_addons_payment", payload)


@router.post("", response_model=PaymentLinkCreateResponse)
async def create_link(
    body: PaymentLinkCreateRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Создаёт платёжную ссылку для текущего авторизованного пользователя."""
    billing_user_ref = await idb.ensure_billing_user_for_identity(session, identity)
    web_metadata = dict(body.metadata or {})
    web_metadata.setdefault("payment_flow", "balance_topup")
    await _validate_payment_intent(session, billing_user_ref, web_metadata)
    payment_request = PaymentLinkRequest(
        legacy_user_ref=billing_user_ref,
        amount=body.amount,
        currency=body.currency or "RUB",
        provider_id=body.provider_id or resolve_default_web_payment_provider() or "",
        success_url=body.success_url,
        failure_url=body.failure_url,
        metadata=web_metadata,
        accepted_gross_amount=body.accepted_gross_amount,
    )
    if str(body.provider_id or "").upper() not in {
        "YOOKASSA_AUTOPAY",
        "YOOKASSA_AUTOPAY_WEB",
        "KASSA2328",
    }:
        try:
            await _store_payment_intent(
                session=session,
                billing_user_ref=billing_user_ref,
                metadata=web_metadata,
                amount=body.amount,
            )
        except ServiceError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Некорректные данные платежа") from None
    await session.commit()
    result = await create_payment_link(session, payment_request)
    return PaymentLinkCreateResponse(
        success=result.success,
        payment_id=result.payment_id,
        payment_url=result.payment_url,
        error=result.error,
        amount_quote=getattr(result, "amount_quote", None),
    )


@router.get("/stream")
async def payment_events_stream(request: Request):
    from api.depends import _identity_from_cookie

    async with async_session_maker() as session:
        identity = await _identity_from_cookie(session, request)
        if identity is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        billing_user_ref = await idb.ensure_billing_user_for_identity(session, identity)
        await session.commit()

    async def event_generator():
        redis_client = None
        pubsub = None
        channel = payment_events_channel(int(billing_user_ref))
        try:
            from redis.asyncio import from_url

            redis_client = from_url(REDIS_URL, encoding="utf-8", decode_responses=True, max_connections=8)
            pubsub = redis_client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(channel)
            logger.info(f"[Site:Pay] Клиент {billing_user_ref} ждёт ответа кассы (канал {channel})")
            yield "retry: 1500\n\n"
            while True:
                if await request.is_disconnected():
                    logger.info(f"[Site:Pay] Клиент {billing_user_ref} закрыл ожидание платежа")
                    break
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if message and message.get("type") == "message":
                    raw_data = message.get("data")
                    payload = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                    if isinstance(payload, dict):
                        logger.info(
                            f"[Site:Pay] Клиенту {billing_user_ref} ушёл статус платежа: "
                            f"{payload.get('status')}, сценарий {payload.get('flow')}"
                        )
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        continue
                yield ": keepalive\n\n"
                await asyncio.sleep(0.1)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(channel)
                    await pubsub.close()
                except Exception:
                    pass
            if redis_client is not None:
                try:
                    await redis_client.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{payment_id}", response_model=PaymentLinkStatusResponse)
async def get_link_status(
    payment_id: str,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    billing_user_ref = await idb.ensure_billing_user_for_identity(session, identity)
    payment = await get_payment_from_db_by_payment_id(session, payment_id)
    if payment is None:
        payment = await get_payment_by_payment_id(session, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    owner_ref = payment.get("user_id")
    if owner_ref is None and payment.get("tg_id") is not None:
        owner = await resolve_user_optional(session, TelegramId(payment["tg_id"]))
        owner_ref = owner.id if owner is not None else None
    if owner_ref is None or int(owner_ref) != int(billing_user_ref):
        raise HTTPException(status_code=404, detail="Payment not found")
    status = str(payment.get("status") or "").lower() or None
    provider_manages_expiry = str(payment.get("payment_system") or "").upper() in {
        "YOOKASSA_AUTOPAY",
        "YOOKASSA_AUTOPAY_WEB",
        "2328",
    }
    if status not in {"success", "failed", "cancelled", "refunded", "chargebacked"} and (
        provider_manages_expiry or not _payment_link_expired(payment.get("created_at"))
    ):
        from services.payments.reconcile import reconcile_pending_payment

        try:
            await session.commit()
            await reconcile_pending_payment(payment)
            current = await get_payment_from_db_by_payment_id(session, payment_id)
            status = str((current or {}).get("status") or "").lower() or status
        except Exception as e:
            logger.warning(f"[Site:Pay] Не удалось сверить платёж {payment_id} с кассой: {e}")
    return PaymentLinkStatusResponse(
        success=True,
        payment_id=payment_id,
        status=status,
        completed=status in {"success", "failed", "cancelled", "refunded", "chargebacked"},
        paid=status == "success",
    )
