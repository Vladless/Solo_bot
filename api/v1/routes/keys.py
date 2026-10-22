from datetime import datetime

from fastapi import Body, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import (
    require_admin_action,
    require_key_update_permissions,
)
from api.depends import get_session, verify_admin_token
from api.v1.routes.base_crud import generate_crud_router
from api.v1.schemas.keys import KeyBase, KeyCreateRequest, KeyResponse, KeyUpdate
from database.access.resolution import TelegramId, resolve_user_optional
from database.models import Admin, Key, Tariff
from filters.permissions import (
    PERM_KEY_CREATE,
    PERM_KEY_DELETE,
    PERM_KEY_TRAFFIC,
    PERM_KEY_VIEW,
)
from logger import logger
from services.operations import create_key_on_cluster, delete_key_from_cluster, renew_key_in_cluster


router = generate_crud_router(
    model=Key,
    schema_response=KeyResponse,
    schema_create=KeyBase,
    schema_update=KeyUpdate,
    identifier_field="user_id",
    parameter_name="tg_id",
    telegram_path_to_user_id=True,
    extra_get_by_email=True,
    enabled_methods=["get_all", "get_one", "get_by_email", "get_all_by_field"],
)


@router.delete("/by_email/{email}", response_model=dict)
async def delete_key_by_email(
    email: str = Path(..., description="Email клиента"),
    session: AsyncSession = Depends(get_session),
    admin: Admin = Depends(verify_admin_token),
):
    await require_admin_action(session, admin, PERM_KEY_DELETE)
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
        logger.info(f"[Site:Subs] Подписка удалена: {db_key.client_id}")
        return {"message": "Ключ успешно удалён"}

    except Exception as e:
        logger.error(f"[Site:Subs] Не удалось удалить подписку: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при удалении ключа")


@router.get("/routers/{tg_id}", response_model=list[KeyResponse])
async def get_router_keys_by_tg_id(
    tg_id: int = Path(..., description="Telegram ID пользователя"),
    session: AsyncSession = Depends(get_session),
    admin: Admin = Depends(verify_admin_token),
):
    await require_admin_action(session, admin, PERM_KEY_VIEW)
    tariffs_result = await session.execute(select(Tariff.id).where(Tariff.group_code == "routers"))
    tariff_ids = [row[0] for row in tariffs_result.all()]
    if not tariff_ids:
        return []

    u = await resolve_user_optional(session, TelegramId(tg_id))
    if u is None:
        return []
    keys_result = await session.execute(select(Key).where(Key.user_id == u.id, Key.tariff_id.in_(tariff_ids)))
    keys = keys_result.scalars().all()
    return keys


@router.patch("/edit/by_email/{email}", response_model=KeyResponse)
async def edit_key_by_email(
    email: str = Path(..., description="Email клиента"),
    key_update: KeyUpdate = Body(...),
    session: AsyncSession = Depends(get_session),
    admin: Admin = Depends(verify_admin_token),
):
    await require_key_update_permissions(session, admin, key_update.model_dump(exclude_unset=True))
    await require_admin_action(session, admin, PERM_KEY_TRAFFIC, PERM_KEY_VIEW)
    result = await session.execute(select(Key).where(Key.email == email))
    db_key = result.scalar_one_or_none()
    if not db_key:
        raise HTTPException(status_code=404, detail="Ключ не найден")

    for field, value in key_update.model_dump(exclude_unset=True).items():
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
        renewed = await renew_key_in_cluster(
            cluster_id=db_key.server_id,
            email=db_key.email,
            client_id=db_key.client_id,
            new_expiry_time=new_expiry_time,
            total_gb=getattr(db_key, "traffic_limit", None),
            session=session,
            hwid_device_limit=getattr(db_key, "device_limit", None),
            reset_traffic=True,
        )
        if not renewed:
            raise RuntimeError("Изменение подписки на панели не подтверждено")

        logger.info(f"[Site:Subs] Подписка обновлена: {db_key.client_id}")
        return db_key

    except Exception as e:
        logger.error(f"[Site:Subs] Не удалось обновить подписку: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при обновлении ключа")


@router.post("/create", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_key_api(
    payload: KeyCreateRequest = Body(...),
    session: AsyncSession = Depends(get_session),
    admin: Admin = Depends(verify_admin_token),
):
    await require_admin_action(session, admin, PERM_KEY_CREATE)
    try:
        await create_key_on_cluster(
            cluster_id=payload.cluster_id,
            tg_id=TelegramId(payload.tg_id),
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
        logger.error(f"[Site:Subs] Не удалось создать подписку: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при создании ключа")
