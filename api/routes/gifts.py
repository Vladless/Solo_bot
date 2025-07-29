from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token
from api.routes.base_crud import generate_crud_router
from api.schemas import GiftBase, GiftResponse, GiftUpdate, GiftUsageResponse
from database.models import Admin, Gift, GiftUsage


router = APIRouter()


gift_router = generate_crud_router(
    model=Gift,
    schema_response=GiftResponse,
    schema_create=GiftBase,
    schema_update=GiftUpdate,
    identifier_field="gift_id",
    parameter_name="gift_id",
    enabled_methods=["get_all", "get_one", "create", "update", "delete"],
)
router.include_router(gift_router, prefix="", tags=["Gifts"])


@router.get("/by_tg_id/{tg_id}", response_model=list[GiftResponse], tags=["Gifts"])
async def get_gifts_by_tg_id(
    tg_id: int = Path(...),
    admin: Admin = Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Gift).where(Gift.sender_tg_id == tg_id))
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
router.include_router(gift_usage_router, prefix="/usages", tags=["GiftUsages"])
router.include_router(gift_usage_router, prefix="/usages", tags=["Gifts"])
