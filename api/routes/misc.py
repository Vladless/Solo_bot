from fastapi import APIRouter
from api.routes.base_crud import generate_crud_router
from database.models import (
    Payment, Referral, Notification,
    ManualBan, TemporaryData, BlockedUser, TrackingSource
)
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
    tags=["Payments"]
)

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
    tags=["Referrals"]
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
    tags=["Notifications"]
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
    tags=["Bans"]
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
    tags=["Bans"]
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
    tags=["TemporaryData"]
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
    tags=["TrackingSources"]
)
