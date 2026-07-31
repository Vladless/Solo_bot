from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin
from api.v2.base_crud import generate_crud_router
from api.v2.schemas import (
    BlockedUserResponse,
    ManualBanResponse,
    NotificationResponse,
    PaymentResponse,
    TemporaryDataResponse,
    TrackingSourceResponse,
)
from database import get_tracking_source_stats
from database.access.resolution import resolve_user_optional
from database.models import (
    BlockedUser,
    ManualBan,
    Notification,
    Payment,
    TemporaryData,
    TrackingSource,
    User,
)


router = APIRouter()


@router.get("/admin/payments", tags=["Payments"])
async def list_payments_admin(
    q: str = Query(""),
    status: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Постраничный список платежей с поиском (система, tg_id, сумма) и фильтром статуса."""
    stmt = select(Payment, User.tg_id, User.username, User.first_name, User.last_name).join(
        User, Payment.user_id == User.id, isouter=True
    )
    term = q.strip().lstrip("#@")
    if term:
        low = f"%{term.lower()}%"
        like = f"%{term}%"
        stmt = stmt.where(
            or_(
                func.lower(Payment.payment_system).like(low),
                func.lower(func.coalesce(Payment.currency, "")).like(low),
                cast(User.tg_id, Text).like(like),
                cast(Payment.tg_id, Text).like(like),
                cast(Payment.amount, Text).like(like),
                func.lower(func.coalesce(User.username, "")).like(low),
                func.lower(func.coalesce(User.first_name, "")).like(low),
                func.lower(func.coalesce(User.last_name, "")).like(low),
            )
        )
    if status == "success":
        stmt = stmt.where(Payment.status == "success")
    elif status == "pending":
        stmt = stmt.where(Payment.status != "success")
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    rows = (await session.execute(stmt.order_by(Payment.created_at.desc()).limit(limit).offset(offset))).all()
    items = [
        {
            "id": p.id,
            "tg_id": p.tg_id if p.tg_id is not None else tg,
            "amount": float(p.amount or 0),
            "currency": p.currency,
            "payment_system": p.payment_system,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
        }
        for p, tg, username, first_name, last_name in rows
    ]
    return {"total": int(total), "items": items}

router.include_router(
    generate_crud_router(
        model=Payment,
        schema_response=PaymentResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="id",
        enabled_methods=["get_all", "get_one", "delete"],
    ),
    prefix="/payments",
    tags=["Payments"],
    dependencies=[Depends(verify_identity_admin)],
)


@router.get("/payments/by_tg_id/{tg_id}", response_model=list[PaymentResponse], tags=["Payments"])
async def get_payments_by_tg_id(
    tg_id: int = Path(...),
    identity=Depends(verify_identity_admin),
    session: AsyncSession = Depends(get_session),
):
    """Список платежей по tg_id пользователя."""
    u = await resolve_user_optional(session, tg_id)
    if u is None:
        raise HTTPException(status_code=404, detail="Payments not found")
    result = await session.execute(select(Payment).where(Payment.user_id == u.id))
    payments = result.scalars().all()
    if not payments:
        raise HTTPException(status_code=404, detail="Payments not found")
    return payments


router.include_router(
    generate_crud_router(
        model=Notification,
        schema_response=NotificationResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="user_id",
        parameter_name="tg_id",
        telegram_path_to_user_id=True,
        enabled_methods=["get_all", "get_one", "delete"],
    ),
    prefix="/admin/notifications",
    tags=["Notifications"],
    dependencies=[Depends(verify_identity_admin)],
)

router.include_router(
    generate_crud_router(
        model=ManualBan,
        schema_response=ManualBanResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="user_id",
        parameter_name="tg_id",
        telegram_path_to_user_id=True,
        enabled_methods=["get_all", "get_one", "delete"],
    ),
    prefix="/manual-bans",
    tags=["Bans"],
    dependencies=[Depends(verify_identity_admin)],
)

router.include_router(
    generate_crud_router(
        model=BlockedUser,
        schema_response=BlockedUserResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="user_id",
        parameter_name="tg_id",
        telegram_path_to_user_id=True,
        enabled_methods=["get_all", "get_one", "delete"],
    ),
    prefix="/blocked-users",
    tags=["Bans"],
    dependencies=[Depends(verify_identity_admin)],
)

router.include_router(
    generate_crud_router(
        model=TemporaryData,
        schema_response=TemporaryDataResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="user_id",
        parameter_name="tg_id",
        telegram_path_to_user_id=True,
        enabled_methods=["get_all", "get_one", "delete"],
    ),
    prefix="/temporary-data",
    tags=["TemporaryData"],
    dependencies=[Depends(verify_identity_admin)],
)

router.include_router(
    generate_crud_router(
        model=TrackingSource,
        schema_response=TrackingSourceResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="id",
        enabled_methods=["get_all", "delete"],
    ),
    prefix="/tracking-sources",
    tags=["TrackingSources"],
    dependencies=[Depends(verify_identity_admin)],
)


@router.get(
    "/tracking-sources/{code}", response_model=TrackingSourceResponse, dependencies=[Depends(verify_identity_admin)]
)
async def get_tracking_source_with_stats(
    code: str,
    session: AsyncSession = Depends(get_session),
):
    """Источник по коду со статистикой регистраций и платежей."""
    result = await session.execute(select(TrackingSource).where(TrackingSource.code == code))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Tracking source not found")
    stats = await get_tracking_source_stats(session, code)
    return TrackingSourceResponse(
        id=source.id,
        name=source.name,
        code=source.code,
        type=source.type,
        created_by=source.created_by,
        created_at=source.created_at,
        registrations=(stats["registrations"] if stats else 0),
        trials=(stats["trials"] if stats else 0),
        payments=(stats["payments"] if stats else 0),
        total_amount=(float(stats["total_amount"]) if stats else 0.0),
        monthly=(stats["monthly"] if stats and "monthly" in stats else []),
    )
