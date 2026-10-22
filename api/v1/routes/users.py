import asyncio

from fastapi import Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import require_admin_action
from api.depends import get_session, verify_admin_token
from api.v1.routes.base_crud import generate_crud_router
from api.v1.schemas.users import UserBase, UserResponse, UserUpdate
from database import async_session_maker, delete_user_data, get_servers
from database.access.resolution import TelegramId, UserId, resolve_user_optional
from database.models import Key, User
from filters.permissions import PERM_KEY_DELETE
from logger import logger
from services.operations import delete_key_from_cluster


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
    await require_admin_action(session, admin, PERM_KEY_DELETE)
    try:
        u = await resolve_user_optional(session, TelegramId(tg_id))
        if u is None:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        result = await session.execute(select(Key.email, Key.client_id).where(Key.user_id == u.id))
        key_records = result.all()

        async with async_session_maker() as s:
            servers = await get_servers(session=s)
        cluster_ids = list(servers.keys())

        async def _delete_one(cluster_id: str, email: str, client_id: str):
            async with async_session_maker() as s:
                return await delete_key_from_cluster(cluster_id, email, client_id, s)

        tasks = [
            _delete_one(cluster_id, email, client_id) for email, client_id in key_records for cluster_id in cluster_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if (key_records and not cluster_ids) or any(
            isinstance(result, BaseException) or result is False for result in results
        ):
            logger.error(f"[Site:Clients] Не подтверждено удаление подписок клиента {tg_id}: {results}")
            raise HTTPException(
                status_code=502,
                detail="Не удалось удалить все подписки с серверов. Клиент сохранён; повторите удаление.",
            )

        await delete_user_data(session, UserId(u.id))

        return {"detail": f"Пользователь {tg_id} и его ключи успешно удалены."}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Site:Clients] Не удалось удалить клиента {tg_id}: {e}")
        raise HTTPException(status_code=500, detail="Ошибка при удалении пользователя")
