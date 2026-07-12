from datetime import datetime, timedelta
from math import ceil
from urllib.parse import urlsplit

import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pytz import timezone as tz_moscow
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, validate_redirect_url, verify_identity_admin, verify_identity_token
from api.v2.base_crud import generate_crud_router
from api.v2.routes.coupon_pricing import resolve_percent_coupon_pricing
from api.v2.schemas import TariffBase, TariffResponse, TariffUpdate
from api.v2.schemas.tariffs import TariffGroup, TariffPublic
from api.v2.schemas.web_public import (
    TariffConfigPriceResponse,
    TariffPurchaseRequest,
    TariffPurchaseResponse,
)
from config import TRIAL_TIME_DISABLE
from core.bootstrap import MODES_CONFIG, PAYMENTS_CONFIG
from core.redis_cache import cache_get, cache_key, cache_set
from database import (
    get_balance,
    identities as idb,
)
from database.coupons import mark_coupon_used
from database.models import Key, Server, Tariff
from database.tariffs import get_tariff_by_id
from database.temporary_data import create_temporary_data
from handlers.texts import TARIFF_COOLDOWN_MESSAGE
from logger import logger
from services.errors import InsufficientFundsError
from services.keys import create_vpn_key_headless
from services.payments.payment_links import PaymentLinkRequest, create_payment_link
from services.payments.providers import get_web_link_provider_ids
from services.tariffs import calculate_config_price, filter_config_options
from services.tariffs.cooldown import format_cooldown_left, get_tariff_cooldown_remaining
from services.tariffs.visibility import is_tariff_visible_for


def _full_config_options(raw: object) -> list[int] | None:
    """Все настроенные варианты для докупки в вебе."""
    if not isinstance(raw, list):
        return None
    out: list[int] = []
    for value in raw:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    out = sorted(set(out), key=lambda val: (int(val == 0), val))
    return out or None


def _tariff_to_public(t: Tariff) -> TariffPublic:
    dev_opts = getattr(t, "device_options", None)
    tr_opts = getattr(t, "traffic_options_gb", None)
    filtered_devices, filtered_traffic = filter_config_options(t)
    device_options = filtered_devices if isinstance(dev_opts, list) and filtered_devices else None
    traffic_options_gb = filtered_traffic if isinstance(tr_opts, list) and filtered_traffic else None
    addon_device_options = _full_config_options(dev_opts)
    addon_traffic_options = _full_config_options(tr_opts)
    return TariffPublic(
        id=t.id,
        name=t.name or "",
        group_code=t.group_code or "",
        duration_days=t.duration_days or 0,
        price_rub=t.price_rub or 0,
        traffic_limit=t.traffic_limit,
        device_limit=t.device_limit,
        subgroup_title=t.subgroup_title,
        sort_order=t.sort_order,
        vless=bool(getattr(t, "vless", False)),
        configurable=bool(getattr(t, "configurable", False)),
        device_options=device_options,
        traffic_options_gb=traffic_options_gb,
        addon_device_options=addon_device_options,
        addon_traffic_options=addon_traffic_options,
        cooldown_days=int(getattr(t, "cooldown_days", 0) or 0),
    )


public_router = APIRouter()


def _resolve_public_base_url(request: Request) -> str:
    origin = str(request.headers.get("origin") or "").strip()
    if origin.startswith(("http://", "https://")):
        return origin.rstrip("/")
    referer = str(request.headers.get("referer") or request.headers.get("referrer") or "").strip()
    if referer.startswith(("http://", "https://")):
        parsed = urlsplit(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").strip()
    host = forwarded_host or str(request.headers.get("host") or "").strip()
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.url.scheme
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _resolve_default_web_payment_provider() -> str | None:
    ids = get_web_link_provider_ids()
    for provider_id in ids:
        if bool(PAYMENTS_CONFIG.get(provider_id)):
            return provider_id
    return ids[0] if ids else None


def _public_tariffs_cache_key(
    group_code: str | None,
    tariff_ids: str | None,
    filter_vless: str | None,
    site_trial_disabled: bool,
) -> str:
    normalized_group = (group_code or "").strip().lower()
    normalized_ids = ",".join(part.strip() for part in (tariff_ids or "").split(",") if part.strip())
    normalized_vless = (filter_vless or "").strip().lower()
    return cache_key(
        "tariffs_public",
        normalized_group or "-",
        normalized_ids or "-",
        normalized_vless or "-",
        "notrial" if site_trial_disabled else "-",
    )


@public_router.get("/groups", response_model=list[TariffGroup])
async def get_tariff_groups(session: AsyncSession = Depends(get_session)):
    """Публичный список групп тарифов — уникальные значения колонки group_code."""
    q = (
        select(Tariff.group_code)
        .where(Tariff.is_active.is_(True), Tariff.group_code.isnot(None), Tariff.group_code != "")
        .distinct()
        .order_by(Tariff.group_code)
    )
    result = await session.execute(q)
    values = result.scalars().all()
    return [TariffGroup(group_code=v or "") for v in values]


@public_router.get("/public", response_model=list[TariffPublic])
async def get_tariffs_public(
    group_code: str | None = Query(None, description="Фильтр по группе тарифов"),
    tariff_ids: str | None = Query(None, description="ID тарифов через запятую (приоритет над группой)"),
    filter_vless: str | None = Query(
        None,
        description="vless: только для роутера (vless=True), app: только для приложения (vless=False), иначе все",
    ),
    session: AsyncSession = Depends(get_session),
):
    """Публичный список активных тарифов (без авторизации)."""
    site_trial_disabled = bool(MODES_CONFIG.get("TRIAL_TIME_DISABLED", TRIAL_TIME_DISABLE)) or bool(
        MODES_CONFIG.get("WEB_TRIAL_DISABLED", False)
    )
    cache_token = _public_tariffs_cache_key(group_code, tariff_ids, filter_vless, site_trial_disabled)
    cached = await cache_get(cache_token)
    if isinstance(cached, list):
        return cached

    q = (
        select(Tariff)
        .where(Tariff.is_active.is_(True))
        .order_by(Tariff.sort_order.asc().nulls_last(), Tariff.price_rub.asc())
    )
    if tariff_ids:
        try:
            ids = [int(x.strip()) for x in tariff_ids.split(",") if x.strip()]
            if not ids:
                return []
            q = q.where(Tariff.id.in_(ids))
        except ValueError:
            raise HTTPException(status_code=422, detail="Некорректный параметр tariff_ids")
    elif group_code:
        q = q.where(Tariff.group_code == group_code)
    else:
        allowed_groups_subq = (
            select(Server.tariff_group).where(Server.enabled.is_(True), Server.tariff_group.isnot(None)).distinct()
        )
        q = q.where(Tariff.group_code.in_(allowed_groups_subq))
    if filter_vless == "router":
        q = q.where(Tariff.vless.is_(True))
    elif filter_vless == "app":
        q = q.where(Tariff.vless.is_(False))
    if site_trial_disabled:
        q = q.where((Tariff.group_code.is_(None)) | (Tariff.group_code != "trial"))
    result = await session.execute(q)
    rows = result.scalars().all()
    payload = [_tariff_to_public(t).model_dump() for t in rows]
    await cache_set(cache_token, payload, 30)
    return payload


@public_router.get("/config-price", response_model=TariffConfigPriceResponse)
async def get_tariff_config_price(
    tariff_id: int = Query(..., ge=1),
    selected_device_limit: int | None = Query(None),
    selected_traffic_gb: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff or not tariff.get("is_active", True):
        raise HTTPException(status_code=404, detail="Тариф не найден")
    price = int(calculate_config_price(tariff, selected_device_limit, selected_traffic_gb))
    return TariffConfigPriceResponse(price_rub=price)


user_tariff_router = APIRouter()


@user_tariff_router.post("/purchase", response_model=TariffPurchaseResponse)
async def purchase_tariff_with_balance(
    body: TariffPurchaseRequest,
    request: Request,
    preview: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    tg_id = await idb.ensure_billing_user_for_identity(session, identity)
    tariff = await get_tariff_by_id(session, body.tariff_id)
    if not tariff or not tariff.get("is_active", True):
        raise HTTPException(status_code=404, detail="Тариф не найден")
    tariff_group_code = (tariff.get("group_code") or "").strip()
    if not tariff_group_code:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    tariff_is_purchasable = await session.scalar(
        select(Server.id).where(Server.tariff_group == tariff_group_code, Server.enabled.is_(True)).limit(1)
    )
    if not tariff_is_purchasable:
        raise HTTPException(status_code=404, detail="Тариф не найден")
    if not preview:
        if not await is_tariff_visible_for(session, int(tg_id), tariff):
            raise HTTPException(status_code=404, detail="Тариф недоступен")
        cooldown_left = await get_tariff_cooldown_remaining(
            session, int(tg_id), int(body.tariff_id), int(tariff.get("cooldown_days") or 0)
        )
        if cooldown_left > 0:
            raise HTTPException(
                status_code=429,
                detail=TARIFF_COOLDOWN_MESSAGE.format(
                    days=int(tariff.get("cooldown_days") or 0),
                    left=format_cooldown_left(cooldown_left),
                ),
            )
    price = int(calculate_config_price(tariff, body.selected_device_limit, body.selected_traffic_gb))
    if price <= 0:
        raise HTTPException(status_code=400, detail="Некорректная цена тарифа")
    final_price, discount_rub, coupon_id, applied_coupon_code = await resolve_percent_coupon_pricing(
        session=session,
        billing_user_id=int(tg_id),
        base_price_rub=int(price),
        coupon_code=body.coupon_code,
    )
    balance = float(await get_balance(session, tg_id))
    duration = int(tariff.get("duration_days") or 0)
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Некорректная длительность тарифа")
    required_amount = int(max(0, ceil(float(final_price) - balance)))
    if preview:
        return TariffPurchaseResponse(
            ok=True,
            message="Расчет обновлен",
            key_email=None,
            charged_rub=0,
            base_price_rub=int(price),
            discount_rub=int(discount_rub),
            final_price_rub=int(final_price),
            applied_coupon_code=applied_coupon_code,
            payment_required=required_amount > 0,
            required_amount_rub=int(required_amount),
            payment_id=None,
            payment_url=None,
        )
    if required_amount > 0:
        provider_id = str(body.provider_id or _resolve_default_web_payment_provider() or "").strip().upper()
        if not provider_id:
            raise HTTPException(status_code=503, detail="Нет доступных провайдеров оплаты")
        base_url = _resolve_public_base_url(request)
        success_url = validate_redirect_url(str(body.success_url or ""), f"{base_url}/payment-success")
        failure_url = validate_redirect_url(str(body.failure_url or ""), f"{base_url}/payment-failure")
        payment_request = PaymentLinkRequest(
            legacy_user_ref=int(tg_id),
            amount=required_amount,
            currency="RUB",
            provider_id=provider_id,
            success_url=success_url,
            failure_url=failure_url,
            metadata={
                "payment_flow": "tariff_purchase",
                "tariff_id": int(body.tariff_id),
                "selected_device_limit": body.selected_device_limit,
                "selected_traffic_gb": body.selected_traffic_gb,
                "selected_duration_days": int(duration),
                "selected_price_rub": int(final_price),
                "base_price_rub": int(price),
                "discount_rub": int(discount_rub),
                "applied_coupon_code": applied_coupon_code,
                "coupon_id": int(coupon_id) if coupon_id is not None else None,
            },
        )
        payment_result = await create_payment_link(session, payment_request)
        if not payment_result.success or not payment_result.payment_url or not payment_result.payment_id:
            raise HTTPException(status_code=400, detail=payment_result.error or "Не удалось создать ссылку оплаты")
        await create_temporary_data(
            session,
            int(tg_id),
            "waiting_for_payment",
            {
                "tariff_id": int(body.tariff_id),
                "required_amount": int(required_amount),
                "selected_price_rub": int(final_price),
                "selected_device_limit": body.selected_device_limit,
                "selected_traffic_limit_gb": body.selected_traffic_gb,
                "selected_duration_days": int(duration),
                "base_price_rub": int(price),
                "discount_rub": int(discount_rub),
                "applied_coupon_code": applied_coupon_code,
                "coupon_id": int(coupon_id) if coupon_id is not None else None,
            },
        )
        return TariffPurchaseResponse(
            ok=True,
            message="Требуется оплата для оформления подписки",
            key_email=None,
            charged_rub=0,
            base_price_rub=int(price),
            discount_rub=int(discount_rub),
            final_price_rub=int(final_price),
            applied_coupon_code=applied_coupon_code,
            payment_required=True,
            required_amount_rub=required_amount,
            payment_id=payment_result.payment_id,
            payment_url=payment_result.payment_url,
        )
    moscow_tz = tz_moscow("Europe/Moscow")
    expiry = datetime.now(moscow_tz) + timedelta(days=duration)
    try:
        await create_vpn_key_headless(
            session=session,
            tg_id=tg_id,
            expiry_time=expiry,
            plan=body.tariff_id,
            selected_device_limit=body.selected_device_limit,
            selected_traffic_gb=body.selected_traffic_gb,
            selected_price_rub=final_price,
        )
        if coupon_id is not None:
            await mark_coupon_used(session, int(coupon_id), int(tg_id))
    except InsufficientFundsError:
        raise HTTPException(status_code=402, detail="Недостаточно средств на балансе") from None
    except Exception:
        logger.exception("web tariff purchase failed")
        raise HTTPException(status_code=500, detail="Не удалось оформить подписку") from None
    return TariffPurchaseResponse(
        ok=True,
        message="Подписка оформлена. Ключ в разделе «Мои ключи».",
        key_email=None,
        charged_rub=final_price,
        base_price_rub=int(price),
        discount_rub=int(discount_rub),
        final_price_rub=int(final_price),
        applied_coupon_code=applied_coupon_code,
    )


@user_tariff_router.post("/trial", response_model=TariffPurchaseResponse)
async def activate_trial(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Активация триала (бесплатного или платного). Доступно 1 раз."""
    from database import get_trial, update_trial
    from database.tariffs import get_tariffs

    trial_disabled = bool(MODES_CONFIG.get("TRIAL_TIME_DISABLED", TRIAL_TIME_DISABLE)) or bool(
        MODES_CONFIG.get("WEB_TRIAL_DISABLED", False)
    )
    if trial_disabled:
        raise HTTPException(status_code=403, detail="Пробный период на сайте недоступен")

    tg_id = await idb.ensure_billing_user_for_identity(session, identity)

    trial_status = await get_trial(session, tg_id)
    if trial_status not in (0, -1):
        raise HTTPException(status_code=409, detail="Пробная подписка уже использована")

    trial_tariffs = await get_tariffs(session, group_code="trial")
    if not trial_tariffs:
        raise HTTPException(status_code=404, detail="Пробный тариф не найден")

    tariff = max(trial_tariffs, key=lambda t: int(t.get("id", 0) or 0))
    price = int(tariff.get("price_rub", 0) or 0)
    duration = int(tariff.get("duration_days") or 0)
    if duration <= 0:
        raise HTTPException(status_code=400, detail="Некорректная длительность триала")

    if price <= 0:
        moscow_tz = tz_moscow("Europe/Moscow")
        expiry = datetime.now(moscow_tz) + timedelta(days=duration)
        try:
            await create_vpn_key_headless(
                session=session,
                tg_id=tg_id,
                expiry_time=expiry,
                plan=int(tariff["id"]),
                selected_price_rub=0,
                skip_balance_charge=True,
                is_trial=True,
            )
            await update_trial(session, tg_id, 1)
        except Exception:
            logger.exception("web trial activation failed")
            raise HTTPException(status_code=500, detail="Ошибка активации триала") from None
        return TariffPurchaseResponse(
            ok=True,
            message="Пробная подписка активирована!",
            charged_rub=0,
            base_price_rub=0,
            final_price_rub=0,
        )

    balance = float(await get_balance(session, tg_id))
    required_amount = int(max(0, ceil(float(price) - balance)))

    if required_amount <= 0:
        moscow_tz = tz_moscow("Europe/Moscow")
        expiry = datetime.now(moscow_tz) + timedelta(days=duration)
        try:
            await create_vpn_key_headless(
                session=session,
                tg_id=tg_id,
                expiry_time=expiry,
                plan=int(tariff["id"]),
                selected_price_rub=price,
                is_trial=True,
            )
            await update_trial(session, tg_id, 1)
        except InsufficientFundsError:
            raise HTTPException(status_code=402, detail="Недостаточно средств на балансе") from None
        except Exception:
            logger.exception("web paid trial activation failed")
            raise HTTPException(status_code=500, detail="Ошибка активации триала") from None
        return TariffPurchaseResponse(
            ok=True,
            message="Пробная подписка активирована!",
            charged_rub=price,
            base_price_rub=price,
            final_price_rub=price,
        )

    provider_id = str(_resolve_default_web_payment_provider() or "").strip().upper()
    if not provider_id:
        raise HTTPException(status_code=503, detail="Нет доступных провайдеров оплаты")
    base_url = _resolve_public_base_url(request)
    payment_request = PaymentLinkRequest(
        legacy_user_ref=int(tg_id),
        amount=required_amount,
        currency="RUB",
        provider_id=provider_id,
        success_url=f"{base_url}/payment-success",
        failure_url=f"{base_url}/payment-failure",
        metadata={
            "payment_flow": "trial_purchase",
            "tariff_id": int(tariff["id"]),
            "selected_price_rub": price,
            "selected_duration_days": duration,
        },
    )
    payment_result = await create_payment_link(session, payment_request)
    if not payment_result.success or not payment_result.payment_url or not payment_result.payment_id:
        raise HTTPException(status_code=400, detail=payment_result.error or "Не удалось создать ссылку оплаты")
    await create_temporary_data(
        session,
        int(tg_id),
        "waiting_for_payment",
        {
            "payment_flow": "trial_purchase",
            "tariff_id": int(tariff["id"]),
            "required_amount": required_amount,
            "selected_price_rub": price,
            "selected_duration_days": duration,
        },
    )
    return TariffPurchaseResponse(
        ok=True,
        message="Требуется оплата для активации пробной подписки",
        charged_rub=0,
        base_price_rub=price,
        final_price_rub=price,
        payment_required=True,
        required_amount_rub=required_amount,
        payment_id=payment_result.payment_id,
        payment_url=payment_result.payment_url,
    )


stats_router = APIRouter()


@stats_router.get("/stats")
async def tariff_stats(
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Метрики тарифов: активных клиентов на каждый тариф и топ по популярности."""
    now_ms = int(time.time() * 1000)
    client_rows = (
        await session.execute(
            select(Key.tariff_id, func.count())
            .where(Key.tariff_id.is_not(None), Key.expiry_time > now_ms)
            .group_by(Key.tariff_id)
        )
    ).all()
    top_rows = (
        await session.execute(
            select(Tariff.name, func.count(Key.client_id))
            .join(Key, Key.tariff_id == Tariff.id)
            .where(Key.expiry_time > now_ms, Key.is_frozen.is_(False))
            .group_by(Tariff.id, Tariff.name)
            .order_by(func.count(Key.client_id).desc())
            .limit(10)
        )
    ).all()
    return {
        "tariff_clients": {str(tid): int(c) for tid, c in client_rows if tid is not None},
        "top": [{"name": (n or "—"), "clients": int(c or 0)} for n, c in top_rows],
    }


router = generate_crud_router(
    model=Tariff,
    schema_response=TariffResponse,
    schema_create=TariffBase,
    schema_update=TariffUpdate,
    identifier_field="id",
    parameter_name="id",
    enabled_methods=["get_all", "get_one", "create", "update", "delete"],
)
