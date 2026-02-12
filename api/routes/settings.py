from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token
from api.schemas.settings import SettingResponse, SettingUpsert
from database.models import Setting
from database.settings import set_setting


router = APIRouter()


@router.get("/", response_model=list[SettingResponse])
async def get_all_settings(
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Setting))
    return result.scalars().all()


@router.get("/{key}", response_model=SettingResponse)
async def get_setting_by_key(
    key: str,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Setting).where(Setting.key == key))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Setting not found")
    return obj


@router.post("/{key}", response_model=SettingResponse)
async def upsert_setting(
    key: str,
    payload: SettingUpsert,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    obj = await set_setting(
        session=session,
        key=key,
        value=payload.value,
        description=payload.description,
    )
    await session.commit()
    await session.refresh(obj)
    return obj


@router.delete("/{key}", response_model=dict)
async def delete_setting(
    key: str,
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(Setting).where(Setting.key == key))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Setting not found")
    await session.delete(obj)
    await session.commit()
    return {"detail": "Setting deleted"}
