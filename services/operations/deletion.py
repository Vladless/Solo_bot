import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_servers
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import delete_client, get_xui_instance
from panels.remnawave_runtime import remnawave_api
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED, SUPERNODE

from .utils import unique_by_api_url


async def delete_key_from_cluster(cluster_id: str, email: str, client_id: str, session: AsyncSession):
    """Удаляет ключ только с подтверждением всех панелей кластера."""
    try:
        servers = await get_servers(session)
        cluster = servers.get(cluster_id)
        if not cluster:
            cluster = [
                server
                for server_list in servers.values()
                for server in server_list
                if server.get("server_name", "").lower() == cluster_id.lower()
            ]
        if not cluster:
            raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")

        remna_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"]
        xui_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "3x-ui"]
        if len(remna_servers) + len(xui_servers) != len(cluster):
            raise ValueError("Неизвестный тип панели при удалении подписки")
        results = await asyncio.gather(
            delete_on_3xui(xui_servers, email, client_id),
            delete_on_remnawave(remna_servers, client_id, email),
            return_exceptions=True,
        )
        if any(result is not True for result in results):
            raise ValueError("Удаление подписки подтверждено не на всех панелях")
        return True
    except Exception as exc:
        logger.error(f"Ошибка при удалении ключа {client_id} из кластера/сервера {cluster_id}: {exc}")
        raise


async def delete_on_3xui(servers: list, email: str, client_id: str) -> bool:
    """Подтверждает удаление на всех входящих подключениях."""

    async def delete_one(server):
        name = server.get("server_name", "unknown")
        try:
            inbound_id = server.get("inbound_id")
            if not inbound_id or not server.get("api_url"):
                raise ValueError("Не заданы адрес панели или inbound")
            inbound_id = int(inbound_id)
            if inbound_id <= 0:
                raise ValueError("Некорректный inbound")
            xui = await get_xui_instance(server["api_url"])
            unique_email = f"{email}_{name.lower()}" if SUPERNODE else email
            return await delete_client(xui=xui, inbound_id=int(inbound_id), email=unique_email, client_id=client_id)
        except Exception as exc:
            logger.warning(f"{PANEL_XUI} [{name}] Удаление не подтверждено: {exc}")
            return False

    results = await asyncio.gather(*(delete_one(server) for server in servers))
    if any(result is not True for result in results):
        raise ValueError("Удаление подписки подтверждено не на всех серверах 3x-ui")
    return True


async def delete_on_remnawave(servers: list, client_id: str, username: str | None = None) -> bool:
    """Подтверждает удаление на каждой отдельной панели Remnawave."""
    if any(not str(server.get("api_url") or "").strip().rstrip("/") for server in servers):
        raise ValueError("Не задан адрес панели Remnawave")

    async def delete_one(server):
        name = server.get("server_name", "remna")
        try:
            async with remnawave_api(server["api_url"]) as api:
                if not REMNAWAVE_TOKEN_LOGIN_ENABLED:
                    if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                        raise ValueError("Авторизация не удалась")
                if await api.delete_user(client_id, username=username) is not True:
                    raise ValueError("Панель не подтвердила удаление")
                logger.info(f"{PANEL_REMNA} [{name}] Клиент {client_id} удалён")
                return True
        except Exception as exc:
            logger.warning(f"{PANEL_REMNA} [{name}] Удаление не подтверждено: {exc}")
            return False

    results = await asyncio.gather(*(delete_one(server) for server in unique_by_api_url(servers)))
    if any(result is not True for result in results):
        raise ValueError("Удаление подписки подтверждено не на всех панелях Remnawave")
    return True
