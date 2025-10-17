import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
from database import get_servers
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import delete_client, get_xui_instance
from panels.remnawave import RemnawaveAPI

from .utils import unique_by_api_url


async def delete_key_from_cluster(cluster_id: str, email: str, client_id: str, session: AsyncSession):
    try:
        servers = await get_servers(session)
        cluster = servers.get(cluster_id)

        if not cluster:
            found_servers = []
            for _, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        found_servers.append(server_info)
            if found_servers:
                cluster = found_servers
            else:
                raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")

        remna_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"]
        xui_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "3x-ui"]

        await asyncio.gather(
            delete_on_3xui(xui_servers, email, client_id),
            delete_on_remnawave(remna_servers, client_id),
            return_exceptions=True,
        )

    except Exception as e:
        logger.error(f"Ошибка при удалении ключа {client_id} из кластера/сервера {cluster_id}: {e}")
        raise


async def delete_on_3xui(servers: list, email: str, client_id: str):
    tasks = []
    for s in servers:
        name = s.get("server_name", "unknown")
        inbound_id = s.get("inbound_id")
        if not inbound_id:
            logger.warning(f"{PANEL_XUI} [{name}] INBOUND_ID отсутствует при удалении")
            continue
        try:
            xui = await get_xui_instance(s["api_url"])
        except Exception as e:
            logger.warning(f"{PANEL_XUI} [{name}] недоступна панель 3x-ui при удалении: {e}")
            continue
        tasks.append(
            delete_client(
                xui=xui,
                inbound_id=int(inbound_id),
                email=email,
                client_id=client_id,
            )
        )
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def delete_on_remnawave(servers: list, client_id: str) -> bool:
    servers = unique_by_api_url(servers)
    for s in servers:
        name = s.get("server_name", "remna")
        api = RemnawaveAPI(s.get("api_url"))
        ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
        if not ok:
            logger.warning(f"{PANEL_REMNA} [{name}] Авторизация не удалась")
            continue
        try:
            done = await api.delete_user(client_id)
            if done:
                logger.info(f"{PANEL_REMNA} [{name}] Клиент {client_id} удалён")
                return True
        except Exception as e:
            msg = str(e).lower()
            if "not found" in msg or "не найден" in msg or "404" in msg:
                logger.info(f"{PANEL_REMNA} [{name}] Клиент {client_id} не найден")
            else:
                logger.warning(f"{PANEL_REMNA} [{name}] Ошибка удаления клиента {client_id}: {e}")
    return False
