from fastapi import APIRouter

from api.routes.base_crud import generate_crud_router
from api.schemas import CouponBase, CouponResponse, CouponUpdate
from database.models import Coupon

router: APIRouter = generate_crud_router(
    model=Coupon,
    schema_response=CouponResponse,
    schema_create=CouponBase,
    schema_update=CouponUpdate,
    identifier_field="code",
    parameter_name="code",
    enabled_methods=["get_all", "get_one", "create", "update", "delete"],
)
