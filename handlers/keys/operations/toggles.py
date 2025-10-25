import asyncio

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from database import get_servers
from logger import logger
from panels._3xui import get_xui_instance, toggle_client
from panels.remnawave import RemnawaveAPI


async def toggle_client_on_cluster(
    cluster_id: str,
    email: str,
    client_id: str,
    enable: bool = True,
    session: AsyncSession = None,
) -> dict[str, Any]:
    try:
        if session is None:
            raise ValueError("[Cluster Toggle] Не передан объект сессии для toggle_client_on_cluster")
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
                raise ValueError(f"Кластер или сервер с ID/именем '{cluster_id}' не найден.")

        results = {}
        tasks = []

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")

            if panel_type == "3x-ui":
                inbound_id = server_info.get("inbound_id")
                if not inbound_id:
                    logger.warning(f"[3x-ui] INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                    results[server_name] = False
                    continue

                xui = await get_xui_instance(server_info["api_url"])
                unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email

                tasks.append(toggle_client(xui, int(inbound_id), unique_email, client_id, enable))

            elif panel_type == "remnawave":
                remna = RemnawaveAPI(server_info["api_url"])
                if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    logger.error(f"[Remnawave] Авторизация не удалась на сервере {server_name}")
                    results[server_name] = False
                    continue

                func = remna.enable_user if enable else remna.disable_user
                tasks.append(func(client_id))

            else:
                logger.warning(
                    f"[Cluster Toggle] Неизвестный тип панели '{panel_type}' на сервере {server_name}. Пропуск."
                )
                results[server_name] = False

        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for server_info, result in zip(cluster, task_results, strict=False):
            server_name = server_info.get("server_name", "unknown")
            if isinstance(result, Exception):
                logger.error(f"[Cluster Toggle] Ошибка на сервере {server_name}: {result}")
                results[server_name] = False
            else:
                results[server_name] = result

        status = "включен" if enable else "отключен"
        logger.info(f"[Cluster Toggle] Клиент {email} {status} на серверах кластера {cluster_id}")
        logger.debug(f"[Cluster Toggle DEBUG] Результаты: {results}")

        return {
            "status": "success" if any(results.values()) else "error",
            "results": results,
        }

    except Exception as e:
        logger.error(f"[Cluster Toggle] Ошибка при изменении состояния клиента {email} в кластере {cluster_id}: {e}")
        return {"status": "error", "error": str(e)}
