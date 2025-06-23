from fastapi import Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import Key, Admin, Tariff
from api.schemas.keys import KeyBase, KeyResponse, KeyUpdate
from api.routes.base_crud import generate_crud_router
from api.depends import get_session, verify_admin_token
from handlers.keys.key_utils import delete_key_from_cluster
from logger import logger

router = generate_crud_router(
    model=Key,
    schema_response=KeyResponse,
    schema_create=KeyBase,
    schema_update=KeyUpdate,
    identifier_field="tg_id",
    extra_get_by_email=True,
    enabled_methods=["get_all", "get_one", "get_by_email", "get_all_by_field"]
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
    tariffs_result = await session.execute(
        select(Tariff.id).where(Tariff.group_code == "routers")
    )
    tariff_ids = [row[0] for row in tariffs_result.all()]
    if not tariff_ids:
        return []

    keys_result = await session.execute(
        select(Key).where(Key.tg_id == tg_id, Key.tariff_id.in_(tariff_ids))
    )
    keys = keys_result.scalars().all()
    return keys
