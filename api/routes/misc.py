from fastapi import APIRouter, Path, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from api.routes.base_crud import generate_crud_router
from database.models import (
    Payment, Referral, Notification,
    ManualBan, TemporaryData, BlockedUser, TrackingSource, Admin
)
from api.depends import get_session, verify_admin_token
from api.schemas import (
    PaymentResponse, ReferralResponse, NotificationResponse,
    ManualBanResponse, TemporaryDataResponse, BlockedUserResponse,
    TrackingSourceResponse
)

router = APIRouter()

router.include_router(
    generate_crud_router(
        model=Payment,
        schema_response=PaymentResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="id",
        enabled_methods=["get_all", "get_one", "delete"]
    ),
    prefix="/payments",
    tags=["Payments"],
    dependencies=[Depends(verify_admin_token)]
)


@router.get("/payments/by_tg_id/{tg_id}", response_model=list[PaymentResponse], tags=["Payments"])
async def get_payments_by_tg_id(
    tg_id: int = Path(...),
    admin: Admin = Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Payment).where(Payment.tg_id == tg_id))
    payments = result.scalars().all()
    if not payments:
        raise HTTPException(status_code=404, detail="Payments not found")
    return payments


router.include_router(
    generate_crud_router(
        model=Referral,
        schema_response=ReferralResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="referred_tg_id",
        enabled_methods=["get_all", "get_one", "delete"]
    ),
    prefix="/referrals",
    tags=["Referrals"],
    dependencies=[Depends(verify_admin_token)]
)

router.include_router(
    generate_crud_router(
        model=Notification,
        schema_response=NotificationResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="tg_id",
        enabled_methods=["get_all", "get_one", "delete"]
    ),
    prefix="/notifications",
    tags=["Notifications"],
    dependencies=[Depends(verify_admin_token)]
)


router.include_router(
    generate_crud_router(
        model=ManualBan,
        schema_response=ManualBanResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="tg_id",
        enabled_methods=["get_all", "get_one", "delete"]
    ),
    prefix="/manual-bans",
    tags=["Bans"],
    dependencies=[Depends(verify_admin_token)]
)

router.include_router(
    generate_crud_router(
        model=BlockedUser,
        schema_response=BlockedUserResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="tg_id",
        enabled_methods=["get_all", "get_one", "delete"]
    ),
    prefix="/blocked-users",
    tags=["Bans"],
    dependencies=[Depends(verify_admin_token)]
)

router.include_router(
    generate_crud_router(
        model=TemporaryData,
        schema_response=TemporaryDataResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="tg_id",
        enabled_methods=["get_all", "get_one", "delete"]
    ),
    prefix="/temporary-data",
    tags=["TemporaryData"],
    dependencies=[Depends(verify_admin_token)]
)

router.include_router(
    generate_crud_router(
        model=TrackingSource,
        schema_response=TrackingSourceResponse,
        schema_create=None,
        schema_update=None,
        identifier_field="id",
        enabled_methods=["get_all", "get_one", "delete"]
    ),
    prefix="/tracking-sources",
    tags=["TrackingSources"],
    dependencies=[Depends(verify_admin_token)]
)
