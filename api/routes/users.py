import asyncio

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_admin_token
from api.routes.base_crud import generate_crud_router
from api.schemas.users import UserBase, UserResponse, UserUpdate
from database import delete_user_data, get_servers
from database.models import Key, User
from handlers.keys.operations import delete_key_from_cluster
from logger import logger


router = generate_crud_router(
    model=User,
    schema_response=UserResponse,
    schema_create=UserBase,
    schema_update=UserUpdate,
    identifier_field="tg_id",
    enabled_methods=["get_all", "get_one", "get_by_email", "create", "update"],
)


@router.delete("/{tg_id}", response_model=dict)
async def delete_user(
    tg_id: int = Path(..., description="Telegram ID пользователя"),
    admin=Depends(verify_admin_token),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await session.execute(select(Key.email, Key.client_id).where(Key.tg_id == tg_id))
        key_records = result.all()

        async def delete_keys_from_servers():
            try:
                servers = await get_servers(session=session)
                tasks = []
                for email, client_id in key_records:
                    for cluster_id in servers:
                        tasks.append(delete_key_from_cluster(cluster_id, email, client_id, session))
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.error(f"[DELETE] Ошибка при удалении ключей с серверов для пользователя {tg_id}: {e}")

        await delete_keys_from_servers()
        await delete_user_data(session, tg_id)

        return {"detail": f"Пользователь {tg_id} и его ключи успешно удалены."}

    except Exception as e:
        logger.error(f"[DELETE] Ошибка при удалении пользователя {tg_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при удалении пользователя")
