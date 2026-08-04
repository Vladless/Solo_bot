from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_request_actor, get_session, verify_identity_admin, verify_identity_token
from api.v2.base_crud import generate_crud_router
from api.v2.schemas import CouponBase, CouponResponse, CouponUpdate
from api.v2.schemas.web_public import CouponApplyRequest, CouponApplyResponse
from database import identities as idb
from database.models import Coupon, CouponUsage
from services.coupons import apply_fixed_coupon
from services.errors import LimitExceededError, NotFoundError, ServiceError, ValidationError


admin_list_router = APIRouter()


@admin_list_router.get("")
async def list_coupons_admin(
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
    q: str = Query(""),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Постраничный список купонов с поиском по коду (показывает и битые купоны)."""
    stmt = select(Coupon)
    term = q.strip()
    if term:
        stmt = stmt.where(func.lower(Coupon.code).like(f"%{term.lower()}%"))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.order_by(Coupon.id.desc()).limit(limit).offset(offset))).scalars().all()
    items = [
        {
            "id": c.id,
            "code": c.code,
            "amount": c.amount,
            "percent": c.percent,
            "days": c.days,
            "usage_limit": c.usage_limit,
            "usage_count": c.usage_count,
            "is_used": c.is_used,
            "new_users_only": c.new_users_only,
        }
        for c in rows
    ]
    return {"total": int(total), "items": items}


@admin_list_router.get("/stats")
async def coupon_stats(
    days: int = Query(30, ge=1, le=365),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Метрики купонов: использования за период и всего."""
    since = datetime.utcnow() - timedelta(days=days)
    used_period = (
        await session.execute(select(func.count()).select_from(CouponUsage).where(CouponUsage.used_at >= since))
    ).scalar() or 0
    total_coupons = (await session.execute(select(func.count()).select_from(Coupon))).scalar() or 0
    total_redemptions = (await session.execute(select(func.coalesce(func.sum(Coupon.usage_count), 0)))).scalar() or 0
    return {
        "used_in_period": int(used_period),
        "total_coupons": int(total_coupons),
        "total_redemptions": int(total_redemptions),
    }


router = generate_crud_router(
    model=Coupon,
    schema_response=CouponResponse,
    schema_create=CouponBase,
    schema_update=CouponUpdate,
    identifier_field="code",
    parameter_name="code",
    enabled_methods=["get_all", "get_one", "create", "update", "delete"],
)


async def _resolve_coupon_user_id(session: AsyncSession, request: Request, identity) -> tuple[int, int | None]:
    actor = get_request_actor(request)
    billing_user_id = actor.billing_user_id if actor and actor.billing_user_id is not None else None
    if billing_user_id is None:
        billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    tg_id = actor.telegram_chat_id if actor else None
    return int(billing_user_id), tg_id


def _service_error_to_http(e: ServiceError) -> HTTPException:
    status_map = {
        "not_found": 404,
        "limit_exceeded": 409,
        "validation_error": 400,
        "forbidden": 403,
    }
    return HTTPException(status_code=status_map.get(e.code, 400), detail=e.message)


@router.post("/apply", response_model=CouponApplyResponse)
async def apply_coupon(
    body: CouponApplyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    from api.ratelimit import enforce_rate_limit

    await enforce_rate_limit(request, session, bucket="coupon_apply", max_per_window=10, window_sec=60)
    user_id, tg_id = await _resolve_coupon_user_id(session, request, identity)
    try:
        result = await apply_fixed_coupon(
            session=session,
            user_id=user_id,
            tg_id=tg_id,
            code=str(body.code or ""),
        )
        return CouponApplyResponse(
            ok=True,
            message="Купон успешно активирован",
            coupon_code=result.coupon_code,
            amount=result.amount,
            balance=result.balance,
        )
    except ServiceError as e:
        await session.rollback()
        raise _service_error_to_http(e)
    except Exception:
        await session.rollback()
        raise HTTPException(status_code=500, detail="Ошибка активации купона")
