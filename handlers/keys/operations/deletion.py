import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
from database import get_servers
from logger import logger
from panels._3xui import delete_client, get_xui_instance
from panels.remnawave import RemnawaveAPI


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


async def delete_on_3xui(servers: list, email: str, client_id: str):
    tasks = []
    for s in servers:
        name = s.get("server_name", "unknown")
        inbound_id = s.get("inbound_id")
        if not inbound_id:
            logger.warning(f"[{name}] INBOUND_ID отсутствует при удалении")
            continue
        try:
            xui = await get_xui_instance(s["api_url"])
        except Exception as e:
            logger.warning(f"[{name}] недоступна панель 3x-ui при удалении: {e}")
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


async def delete_on_remnawave(servers: list, client_id: str):
    if not client_id:
        return
    for s in servers:
        api = RemnawaveAPI(s["api_url"])
        try:
            ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
            if not ok:
                logger.warning(f"[{s.get('server_name', 'unknown')}] Remnawave API недоступен при удалении")
                continue
            await api.delete_user(client_id)
        except Exception as e:
            logger.warning(f"[{s.get('server_name', 'unknown')}] ошибка удаления Remnawave: {e}")
