from fastapi import APIRouter
from api.routes.base_crud import generate_crud_router
from api.schemas import ReferralResponse
from database.models import Referral

router = generate_crud_router(
    model=Referral,
    schema_response=ReferralResponse,
    schema_create=None,
    schema_update=None,
    identifier_field="referrer_tg_id",
    enabled_methods=["get_all", "get_one", "delete", "get_all_by_field"]
)
