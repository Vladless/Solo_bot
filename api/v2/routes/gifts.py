from base64 import b64encode
from datetime import datetime, timedelta
from io import BytesIO
from math import ceil

import qrcode

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import Text, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    get_request_actor,
    get_session,
    validate_redirect_url,
    verify_identity_admin,
    verify_identity_token,
)
from api.v2.base_crud import generate_crud_router
from api.v2.routes.tariffs import _resolve_default_web_payment_provider, _resolve_public_base_url
from api.v2.schemas import GiftBase, GiftResponse, GiftUpdate, GiftUsageResponse
from api.v2.schemas.web_public import (
    GiftCreatePreviewResponse,
    GiftCreateRequest,
    GiftCreateResponse,
    GiftQrResponse,
    GiftRedeemRequest,
    GiftRedeemResponse,
    GiftUsageEntry,
    MyGiftItem,
    MyGiftsResponse,
)
from config import GIFT_BUTTON
from core.bootstrap import BUTTONS_CONFIG
from database import (
    get_balance,
    identities as idb,
)
from database.access.resolution import resolve_user_optional
from database.models import Gift, GiftUsage, Tariff, User
from database.tariffs import get_tariff_by_id
from database.temporary_data import create_temporary_data
from services.errors import NotFoundError, ValidationError
from services.formatting import get_site_gift_link
from services.gifts import (
    create_gift as service_create_gift,
    redeem_gift as service_redeem_gift,
)
from services.payments.payment_links import PaymentLinkRequest, create_payment_link
from services.tariffs import calculate_config_price


router = APIRouter()


@router.get("/admin-list", tags=["Gifts"])
async def list_gifts_admin(
    q: str = Query(""),
    state: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Постраничный список подарков с поиском (код подарка, tg отправителя/получателя)."""
    stmt = select(Gift)
    term = q.strip().lstrip("#@")
    if term:
        like = f"%{term}%"
        stmt = stmt.where(
            or_(
                func.lower(Gift.gift_id).like(f"%{term.lower()}%"),
                cast(Gift.sender_tg_id, Text).like(like),
                cast(Gift.recipient_tg_id, Text).like(like),
                cast(Gift.selected_price_rub, Text).like(like),
                cast(Gift.selected_months, Text).like(like),
            )
        )
    if state == "used":
        stmt = stmt.where(Gift.is_used.is_(True))
    elif state == "active":
        stmt = stmt.where(Gift.is_used.is_(False))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.order_by(Gift.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    items = [
        {
            "gift_id": g.gift_id,
            "sender_tg_id": g.sender_tg_id,
            "recipient_tg_id": g.recipient_tg_id,
            "selected_months": g.selected_months,
            "selected_price_rub": g.selected_price_rub,
            "is_used": bool(g.is_used),
            "is_unlimited": bool(g.is_unlimited),
            "max_usages": g.max_usages,
            "created_at": g.created_at.isoformat() if g.created_at else None,
            "expiry_time": g.expiry_time.isoformat() if g.expiry_time else None,
        }
        for g in rows
    ]
    return {"total": int(total), "items": items}


stats_router = APIRouter()


@stats_router.get("/stats")
async def gift_stats(
    days: int = Query(30, ge=1, le=365),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Метрики подарков: создано / погашено / конверсия / выручка / активаций за период."""
    since = datetime.utcnow() - timedelta(days=days)
    total = int((await session.execute(select(func.count()).select_from(Gift))).scalar() or 0)
    redeemed = int((await session.execute(select(func.count()).select_from(Gift).where(Gift.is_used.is_(True)))).scalar() or 0)
    activated_period = int((await session.execute(select(func.count()).select_from(GiftUsage).where(GiftUsage.used_at >= since))).scalar() or 0)
    revenue = float((await session.execute(select(func.coalesce(func.sum(Gift.selected_price_rub), 0)).where(Gift.is_used.is_(True)))).scalar() or 0)
    return {
        "total": total,
        "redeemed": redeemed,
        "redemption_rate_pct": round(100.0 * redeemed / total, 1) if total else 0.0,
        "activated_in_period": activated_period,
        "revenue_rub": revenue,
    }


def _check_gifts_enabled():
    if not bool(BUTTONS_CONFIG.get("GIFT_BUTTON_ENABLE", GIFT_BUTTON)):
        raise HTTPException(status_code=403, detail="Подарки отключены")


@router.post("/create", tags=["Gifts"])
async def create_gift_for_user(
    body: GiftCreateRequest,
    request: Request,
    preview: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from api.ratelimit import enforce_rate_limit

    if not preview:
        await enforce_rate_limit(request, session, bucket="gift_create", max_per_window=10, window_sec=60)
    _check_gifts_enabled()
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)

    tariff = await get_tariff_by_id(session, body.tariff_id)
    if not tariff or tariff.get("group_code") != "gifts" or not tariff.get("is_active", True):
        raise HTTPException(status_code=404, detail="Тариф не найден")

    price = int(calculate_config_price(tariff, body.selected_device_limit, body.selected_traffic_gb))
    balance = float(await get_balance(session, billing_user_id))

    required_amount = int(max(0, ceil(float(price) - balance)))

    if preview:
        return GiftCreatePreviewResponse(
            ok=True,
            price_rub=price,
            balance_rub=balance,
            sufficient_funds=balance >= price,
            tariff_name=str(tariff.get("name", "")),
            duration_days=int(tariff.get("duration_days") or 0),
        )

    if required_amount > 0:
        provider_id = str(body.provider_id or _resolve_default_web_payment_provider() or "").strip().upper()
        if not provider_id:
            raise HTTPException(status_code=503, detail="Нет доступных провайдеров оплаты")
        base_url = _resolve_public_base_url(request)
        success_url = validate_redirect_url(str(body.success_url or ""), f"{base_url}/payment-success")
        failure_url = validate_redirect_url(str(body.failure_url or ""), f"{base_url}/payment-failure")
        payment_request = PaymentLinkRequest(
            legacy_user_ref=int(billing_user_id),
            amount=required_amount,
            currency="RUB",
            provider_id=provider_id,
            success_url=success_url,
            failure_url=failure_url,
            metadata={
                "payment_flow": "gift_create",
                "tariff_id": int(body.tariff_id),
                "selected_device_limit": body.selected_device_limit,
                "selected_traffic_gb": body.selected_traffic_gb,
                "selected_price_rub": int(price),
            },
        )
        payment_result = await create_payment_link(session, payment_request)
        if not payment_result.success or not payment_result.payment_url or not payment_result.payment_id:
            raise HTTPException(status_code=400, detail=payment_result.error or "Не удалось создать ссылку оплаты")
        await create_temporary_data(
            session,
            int(billing_user_id),
            "waiting_for_payment",
            {
                "payment_flow": "gift_create",
                "tariff_id": int(body.tariff_id),
                "required_amount": int(required_amount),
                "selected_price_rub": int(price),
                "selected_device_limit": body.selected_device_limit,
                "selected_traffic_gb": body.selected_traffic_gb,
            },
        )
        return GiftCreateResponse(
            ok=True,
            message="Требуется оплата для создания подарка",
            payment_required=True,
            required_amount_rub=required_amount,
            payment_id=payment_result.payment_id,
            payment_url=payment_result.payment_url,
        )

    from services.errors import InsufficientFundsError, NotFoundError

    try:
        result = await service_create_gift(
            session=session,
            sender_user_ref=billing_user_id,
            tariff_id=body.tariff_id,
            selected_device_limit=body.selected_device_limit,
            selected_traffic_gb=body.selected_traffic_gb,
            selected_price_rub=price,
        )
    except InsufficientFundsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None

    new_balance = float(await get_balance(session, billing_user_id))
    return GiftCreateResponse(
        ok=True,
        message=f"Подарок создан — {result.tariff_name} на {result.duration_text}",
        gift_id=result.gift_id,
        site_gift_link=result.site_gift_link,
        tariff_name=result.tariff_name,
        duration_days=result.duration_days,
        price_charged=result.price_charged,
        balance_rub=new_balance,
    )


@router.get("/my", response_model=MyGiftsResponse, tags=["Gifts"])
async def get_my_gifts(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    _check_gifts_enabled()
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)

    base_filter = Gift.sender_user_id == billing_user_id
    total = (await session.execute(select(func.count()).select_from(Gift).where(base_filter))).scalar_one()

    result = await session.execute(
        select(Gift).where(base_filter).order_by(Gift.created_at.desc()).limit(limit).offset(offset)
    )
    gifts = result.scalars().all()

    tariff_ids = {g.tariff_id for g in gifts if g.tariff_id}
    tariff_map: dict[int, str] = {}
    duration_map: dict[int, int] = {}
    if tariff_ids:
        tariff_rows = await session.execute(select(Tariff).where(Tariff.id.in_(tariff_ids)))
        for t in tariff_rows.scalars().all():
            tariff_map[t.id] = t.name or ""
            duration_map[t.id] = int(t.duration_days or 0)

    gift_ids = [g.gift_id for g in gifts]
    usages_map: dict[str, list[GiftUsageEntry]] = {gid: [] for gid in gift_ids}
    if gift_ids:
        usage_rows = await session.execute(select(GiftUsage).where(GiftUsage.gift_id.in_(gift_ids)))
        for u in usage_rows.scalars().all():
            usages_map.setdefault(u.gift_id, []).append(
                GiftUsageEntry(
                    user_id=int(u.user_id),
                    used_at=u.used_at.isoformat() if u.used_at else None,
                )
            )

    items = []
    for g in gifts:
        items.append(
            MyGiftItem(
                gift_id=g.gift_id,
                tariff_name=tariff_map.get(g.tariff_id, ""),
                duration_days=duration_map.get(g.tariff_id, 0),
                price_rub=int(g.selected_price_rub or 0),
                created_at=g.created_at.isoformat() if g.created_at else None,
                expiry_time=g.expiry_time.isoformat() if g.expiry_time else None,
                is_used=bool(g.is_used),
                is_unlimited=bool(g.is_unlimited),
                max_usages=g.max_usages,
                site_gift_link=get_site_gift_link(g.gift_id),
                usages=usages_map.get(g.gift_id, []),
            )
        )

    return MyGiftsResponse(ok=True, gifts=items, total=total, limit=limit, offset=offset)


@router.get("/my/{gift_id}/qr", response_model=GiftQrResponse, tags=["Gifts"])
async def get_my_gift_qr(
    gift_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    _check_gifts_enabled()
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    gift = (
        await session.execute(
            select(Gift).where(Gift.gift_id == gift_id, Gift.sender_user_id == billing_user_id).limit(1)
        )
    ).scalar_one_or_none()
    if gift is None:
        raise HTTPException(status_code=404, detail="Подарок не найден")
    link = get_site_gift_link(gift.gift_id)
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    image_data = b64encode(buffer.getvalue()).decode("ascii")
    return GiftQrResponse(ok=True, link=link, image_data_url=f"data:image/png;base64,{image_data}")


@router.delete("/my/{gift_id}", response_model=dict, tags=["Gifts"])
async def delete_my_gift(
    request: Request,
    gift_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Удаляет свой подарок."""
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)

    result = await session.execute(select(Gift).where(Gift.gift_id == gift_id))
    gift = result.scalar_one_or_none()
    if not gift or gift.sender_user_id != billing_user_id:
        raise HTTPException(status_code=404, detail="Подарок не найден")
    await session.execute(delete(GiftUsage).where(GiftUsage.gift_id == gift_id))
    await session.delete(gift)
    return {"ok": True, "message": "Подарок удалён"}


gift_router = generate_crud_router(
    model=Gift,
    schema_response=GiftResponse,
    schema_create=GiftBase,
    schema_update=GiftUpdate,
    identifier_field="gift_id",
    parameter_name="gift_id",
    enabled_methods=["get_all", "get_one", "create", "update"],
)


@router.post("/redeem", response_model=GiftRedeemResponse, tags=["Gifts"])
async def redeem_gift(
    body: GiftRedeemRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from api.ratelimit import enforce_rate_limit

    await enforce_rate_limit(request, session, bucket="gift_redeem", max_per_window=10, window_sec=60)
    _check_gifts_enabled()
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    try:
        result = await service_redeem_gift(session, body.gift_code, billing_user_id)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except Exception:
        raise HTTPException(status_code=500, detail="Не удалось активировать подарок") from None
    return GiftRedeemResponse(
        ok=True,
        message=result.message,
        gift_id=result.gift_id,
        tariff_id=result.tariff_id,
        duration_days=result.duration_days,
    )


@router.get("/by_tg_id/{tg_id}", response_model=list[GiftResponse], tags=["Gifts"])
async def get_gifts_by_tg_id(
    tg_id: int = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Список подарков по tg_id отправителя."""
    u = await resolve_user_optional(session, tg_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Gifts not found")
    result = await session.execute(select(Gift).where(Gift.sender_user_id == u.id))
    gifts = result.scalars().all()
    if not gifts:
        raise HTTPException(status_code=404, detail="Gifts not found")
    return gifts


gift_usage_router = generate_crud_router(
    model=GiftUsage,
    schema_response=GiftUsageResponse,
    schema_create=None,
    schema_update=None,
    identifier_field="gift_id",
    enabled_methods=["get_all", "get_one", "delete"],
)
router.include_router(gift_usage_router, prefix="/usages", tags=["Gifts", "GiftUsages"])


@router.delete("/{gift_id}", response_model=dict, tags=["Gifts"])
async def delete_gift_with_usages(
    gift_id: str = Path(..., description="ID подарка"),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Удаляет подарок и все его использования."""
    result = await session.execute(select(Gift).where(Gift.gift_id == gift_id))
    gift = result.scalar_one_or_none()
    if not gift:
        raise HTTPException(status_code=404, detail="Gift not found")
    await session.execute(delete(GiftUsage).where(GiftUsage.gift_id == gift_id))
    await session.delete(gift)
    return {"message": "Подарок и связанные использования удалены"}
