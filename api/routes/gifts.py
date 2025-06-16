from fastapi import APIRouter
from api.routes.base_crud import generate_crud_router
from api.schemas import GiftBase, GiftResponse, GiftUpdate, GiftUsageResponse
from database.models import Gift, GiftUsage

router = APIRouter()


gift_router = generate_crud_router(
    model=Gift,
    schema_response=GiftResponse,
    schema_create=GiftBase,
    schema_update=GiftUpdate,
    identifier_field="gift_id",
    parameter_name="gift_id",
    enabled_methods=["get_all", "get_one", "create", "update", "delete"]
)
router.include_router(gift_router, prefix="", tags=["Gifts"])


gift_usage_router = generate_crud_router(
    model=GiftUsage,
    schema_response=GiftUsageResponse,
    schema_create=None,
    schema_update=None,
    identifier_field="gift_id",
    enabled_methods=["get_all", "get_one", "delete"]
)
router.include_router(gift_usage_router, prefix="/usages", tags=["GiftUsages"])
router.include_router(gift_usage_router, prefix="/usages", tags=["Gifts"])

