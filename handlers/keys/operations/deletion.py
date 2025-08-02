from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
from database import get_servers
from logger import logger
from panels.remnawave import RemnawaveAPI
from panels.three_xui import delete_client, get_xui_instance


async def delete_key_from_cluster(cluster_id: str, email: str, client_id: str, session: AsyncSession):
    """Удаление ключа с серверов в кластере или с конкретного сервера"""
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

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")

            if panel_type == "remnawave":
                remna = RemnawaveAPI(server_info["api_url"])
                if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    logger.error(f"[Remnawave] Не удалось войти на сервер {server_name}")
                    continue

                success = await remna.delete_user(client_id)
                if success:
                    logger.info(f"[Remnawave] Клиент {client_id} успешно удалён с {server_name}")
                else:
                    logger.warning(f"[Remnawave] Не удалось удалить клиента {client_id} с {server_name}")

            elif panel_type == "3x-ui":
                xui = await get_xui_instance(server_info["api_url"])
                inbound_id = server_info.get("inbound_id")

                if not inbound_id:
                    logger.warning(f"[3x-ui] INBOUND_ID отсутствует на сервере {server_name}. Пропуск.")
                    continue

                await delete_client(
                    xui,
                    inbound_id=int(inbound_id),
                    email=email,
                    client_id=client_id,
                )
                logger.info(f"[3x-ui] Клиент {client_id} удалён с сервера {server_name}")

            else:
                logger.warning(f"[Unknown] Неизвестный тип панели '{panel_type}' для сервера {server_name}")

    except Exception as e:
        logger.error(f"❌ Ошибка при удалении ключа {client_id} из кластера/сервера {cluster_id}: {e}")
        raise
