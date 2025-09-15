import asyncio

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from database import get_servers
from database.models import Key, Server
from logger import logger
from panels._3xui import get_client_traffic, get_xui_instance
from panels.remnawave import RemnawaveAPI


async def get_user_traffic(session: AsyncSession, tg_id: int, email: str) -> dict[str, Any]:
    """
    Получает трафик пользователя на всех серверах, где у него есть ключ (3x-ui и Remnawave).
    Для Remnawave трафик считается один раз и отображается как "Remnawave (общий):".
    """
    result = await session.execute(select(Key.client_id, Key.server_id).where(Key.tg_id == tg_id, Key.email == email))
    rows = result.all()
    if not rows:
        return {"status": "error", "message": "У пользователя нет активных ключей."}

    server_ids = {row.server_id for row in rows}
    server_id = list(server_ids)[0]

    result = await session.execute(
        select(Server)
        .where(Server.enabled.is_(True))
        .where(Server.server_name.in_(server_ids) | Server.cluster_name.in_(server_ids))
    )
    server_rows = result.scalars().all()
    if not server_rows:
        logger.error(f"Не найдено серверов для: {server_ids}")
        return {
            "status": "error",
            "message": f"Серверы не найдены: {', '.join(server_ids)}",
        }

    servers_map = {
        s.server_name: {
            "server_name": s.server_name,
            "cluster_name": s.cluster_name,
            "api_url": s.api_url,
            "panel_type": s.panel_type,
        }
        for s in server_rows
    }

    user_traffic_data = {}
    tasks = []

    remnawave_client_id = None
    remnawave_checked = False
    remnawave_api_url = None

    async def fetch_traffic(server_info: dict, client_id: str) -> tuple[str, Any]:
        server_name = server_info["server_name"]
        api_url = server_info["api_url"]
        panel_type = server_info.get("panel_type", "3x-ui").lower()

        try:
            if panel_type == "3x-ui":
                xui = await get_xui_instance(api_url)
                traffic_info = await get_client_traffic(xui, client_id)
                if traffic_info["status"] == "success" and traffic_info["traffic"]:
                    client_data = traffic_info["traffic"][0]
                    used_gb = (client_data.up + client_data.down) / 1073741824
                    return server_name, round(used_gb, 2)
                else:
                    return server_name, "Ошибка получения трафика"
            else:
                return server_name, f"Неизвестная панель: {panel_type}"
        except Exception as e:
            return server_name, f"Ошибка: {e}"

    for row in rows:
        client_id = row.client_id
        server_id = row.server_id

        matched_servers = [
            s for s in servers_map.values() if s["server_name"] == server_id or s["cluster_name"] == server_id
        ]
        for server_info in matched_servers:
            panel_type = server_info.get("panel_type", "3x-ui").lower()

            if panel_type == "remnawave" and not remnawave_checked:
                remnawave_client_id = client_id
                remnawave_api_url = server_info["api_url"]
                remnawave_checked = True
            elif panel_type == "3x-ui":
                tasks.append(fetch_traffic(server_info, client_id))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for server, result in results:
        user_traffic_data[server] = result

    if remnawave_client_id and remnawave_api_url:
        try:
            remna = RemnawaveAPI(remnawave_api_url)
            if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                user_traffic_data["Remnawave (общий)"] = "Не удалось авторизоваться"
            else:
                user_data = await remna.get_user_by_uuid(remnawave_client_id)
                if not user_data:
                    user_traffic_data["Remnawave (общий)"] = "Клиент не найден"
                else:
                    used_bytes = user_data.get("usedTrafficBytes", 0)
                    used_gb = round(used_bytes / 1073741824, 2)
                    user_traffic_data["Remnawave (общий)"] = used_gb
        except Exception as e:
            user_traffic_data["Remnawave (общий)"] = f"Ошибка: {e}"

    return {"status": "success", "traffic": user_traffic_data}


async def reset_traffic_in_cluster(cluster_id: str, email: str, session: AsyncSession) -> None:
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

        tasks = []
        remnawave_done = False

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")
            api_url = server_info.get("api_url")
            inbound_id = server_info.get("inbound_id")

            if panel_type == "remnawave" and not remnawave_done:
                result = await session.execute(
                    select(Key.client_id).where(Key.email == email, Key.server_id == cluster_id).limit(1)
                )
                row = result.first()

                if not row:
                    logger.warning(f"[Remnawave Reset] client_id не найден для {email} на {server_name}")
                    continue

                client_id = row[0]

                remna = RemnawaveAPI(api_url)
                if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    logger.warning(f"[Reset Traffic] Не удалось авторизоваться в Remnawave ({server_name})")
                    continue

                tasks.append(remna.reset_user_traffic(client_id))
                remnawave_done = True
                continue

            if panel_type == "3x-ui":
                if not inbound_id:
                    logger.warning(f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                    continue

                xui = await get_xui_instance(api_url)
                unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email
                tasks.append(xui.client.reset_stats(int(inbound_id), unique_email))
            else:
                logger.warning(f"[Reset Traffic] Неизвестный тип панели '{panel_type}' на {server_name}")

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[Reset Traffic] Трафик клиента {email} успешно сброшен в кластере {cluster_id}")

    except Exception as e:
        logger.error(f"[Reset Traffic] Ошибка при сбросе трафика клиента {email} в кластере {cluster_id}: {e}")
        raise
