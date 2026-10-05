from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token
from api.v1.routes.base_crud import generate_crud_router
from api.v1.schemas import ReferralResponse
from database.access.resolution import resolve_user_optional
from database.models import Admin, Referral


router = generate_crud_router(
    model=Referral,
    schema_response=ReferralResponse,
    schema_create=None,
    schema_update=None,
    identifier_field="referrer_user_id",
    parameter_name="referrer_tg_id",
    telegram_path_to_user_id=True,
    enabled_methods=["get_all", "get_one", "get_all_by_field"],
)


@router.delete("/one")
async def delete_one_referral(
    referrer_tg_id: int = Query(..., description="ID пригласившего"),
    referred_tg_id: int = Query(..., description="ID приглашённого"),
    admin: Admin = Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    ru_ref = await resolve_user_optional(session, referrer_tg_id)
    rd_ref = await resolve_user_optional(session, referred_tg_id)
    if ru_ref is None or rd_ref is None:
        raise HTTPException(status_code=404, detail="Referral not found")
    result = await session.execute(
        select(Referral).where(
            Referral.referrer_user_id == ru_ref.id,
            Referral.referred_user_id == rd_ref.id,
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Referral not found")
    await session.delete(obj)
    return {"status": "deleted_one"}
