from datetime import datetime

from fastapi import Body, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token
from api.routes.base_crud import generate_crud_router
from api.schemas.keys import KeyBase, KeyCreateRequest, KeyResponse, KeyUpdate
from database.models import Admin, Key, Tariff
from handlers.keys.operations import create_key_on_cluster, delete_key_from_cluster, renew_key_in_cluster
from logger import logger


router = generate_crud_router(
    model=Key,
    schema_response=KeyResponse,
    schema_create=KeyBase,
    schema_update=KeyUpdate,
    identifier_field="tg_id",
    extra_get_by_email=True,
    enabled_methods=["get_all", "get_one", "get_by_email", "get_all_by_field"],
)


@router.delete("/by_email/{email}", response_model=dict)
async def delete_key_by_email(
    email: str = Path(..., description="Email клиента"),
    session: AsyncSession = Depends(get_session),
    admin: Admin = Depends(verify_admin_token),
):
    result = await session.execute(select(Key).where(Key.email == email))
    db_key = result.scalar_one_or_none()

    if not db_key:
        raise HTTPException(status_code=404, detail="Ключ не найден")

    try:
        await delete_key_from_cluster(
            session=session,
            email=db_key.email,
            client_id=db_key.client_id,
            cluster_id=db_key.server_id,
        )
        await session.delete(db_key)
        await session.commit()
        logger.info(f"[API] Ключ удалён: {db_key.client_id}")
        return {"message": "Ключ успешно удалён"}

    except Exception as e:
        logger.error(f"[API] Ошибка при удалении ключа: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при удалении ключа")


@router.get("/routers/{tg_id}", response_model=list[KeyResponse])
async def get_router_keys_by_tg_id(
    tg_id: int = Path(..., description="Telegram ID пользователя"),
    session: AsyncSession = Depends(get_session),
    admin: Admin = Depends(verify_admin_token),
):
    tariffs_result = await session.execute(select(Tariff.id).where(Tariff.group_code == "routers"))
    tariff_ids = [row[0] for row in tariffs_result.all()]
    if not tariff_ids:
        return []

    keys_result = await session.execute(select(Key).where(Key.tg_id == tg_id, Key.tariff_id.in_(tariff_ids)))
    keys = keys_result.scalars().all()
    return keys


@router.patch("/edit/by_email/{email}", response_model=KeyResponse)
async def edit_key_by_email(
    email: str = Path(..., description="Email клиента"),
    key_update: KeyUpdate = Body(...),
    session: AsyncSession = Depends(get_session),
    admin: Admin = Depends(verify_admin_token),
):
    result = await session.execute(select(Key).where(Key.email == email))
    db_key = result.scalar_one_or_none()
    if not db_key:
        raise HTTPException(status_code=404, detail="Ключ не найден")

    for field, value in key_update.dict(exclude_unset=True).items():
        if field == "expiry_time" and value is not None:
            if isinstance(value, int):
                ms = value
            elif isinstance(value, datetime):
                ms = int(value.timestamp() * 1000)
            else:
                raise HTTPException(status_code=400, detail="Некорректный формат времени")
            setattr(db_key, field, ms)
        else:
            setattr(db_key, field, value)

    try:
        new_expiry_time = db_key.expiry_time
        await renew_key_in_cluster(
            cluster_id=db_key.server_id,
            email=db_key.email,
            client_id=db_key.client_id,
            new_expiry_time=new_expiry_time,
            total_gb=getattr(db_key, "traffic_limit", None),
            session=session,
            hwid_device_limit=getattr(db_key, "device_limit", None),
            reset_traffic=True,
        )
        await session.commit()

        logger.info(f"[API] Ключ обновлён: {db_key.client_id}")
        return db_key

    except Exception as e:
        logger.error(f"[API] Ошибка при обновлении ключа: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при обновлении ключа")


@router.post("/create", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_key_api(
    payload: KeyCreateRequest = Body(...),
    session: AsyncSession = Depends(get_session),
    admin: Admin = Depends(verify_admin_token),
):
    try:
        await create_key_on_cluster(
            cluster_id=payload.cluster_id,
            tg_id=payload.tg_id,
            client_id=payload.client_id,
            email=payload.email or f"{payload.tg_id}_key",
            expiry_timestamp=payload.expiry_timestamp,
            plan=payload.tariff_id,
            session=session,
            remnawave_link=payload.remnawave_link,
            hwid_limit=payload.hwid_limit,
            traffic_limit_bytes=payload.traffic_limit_bytes,
            is_trial=payload.is_trial or False,
        )
        return {"message": "Ключ успешно создан"}

    except Exception as e:
        logger.error(f"[API] Ошибка при создании ключа: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при создании ключа")
