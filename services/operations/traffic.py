import asyncio

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_servers
from database.access.resolution import resolve_user_optional
from database.keys import (
    get_key_client_id_by_email_and_server,
    get_user_keys_with_servers_by_email,
)
from logger import logger
from panels._3xui import get_client_traffic, get_xui_instance
from panels.remnawave_runtime import (
    get_remnawave_profile,
    invalidate_remnawave_profile,
    with_remnawave_api,
)
from settings.config import SUPERNODE


async def get_user_traffic(session: AsyncSession, tg_id: int, email: str) -> dict[str, Any]:
    """
    Получает трафик пользователя на всех серверах, где у него есть ключ (3x-ui и Remnawave).
    Для Remnawave трафик считается один раз и отображается как "Remnawave (общий):".
    Один запрос: Key + Server через join.
    """
    u = await resolve_user_optional(session, tg_id)
    if u is None:
        return {"status": "error", "message": "У пользователя нет активных ключей."}
    rows = await get_user_keys_with_servers_by_email(session, u.id, email)
    if not rows:
        return {"status": "error", "message": "У пользователя нет активных ключей."}

    seen_pairs = set()
    unique_rows = []
    servers_map = {}
    for client_id, server_id, server_info in rows:
        if (client_id, server_id) not in seen_pairs:
            seen_pairs.add((client_id, server_id))
            unique_rows.append((client_id, server_id))
        if server_info["server_name"] not in servers_map:
            servers_map[server_info["server_name"]] = server_info

    user_traffic_data = {}
    tasks = []

    remnawave_client_id = None
    remnawave_checked = False
    remnawave_server_ref = None

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

    for client_id, server_id in unique_rows:
        matched_servers = [
            s for s in servers_map.values() if s["server_name"] == server_id or s["cluster_name"] == server_id
        ]
        for server_info in matched_servers:
            panel_type = server_info.get("panel_type", "3x-ui").lower()

            if panel_type == "remnawave" and not remnawave_checked:
                remnawave_client_id = client_id
                remnawave_server_ref = server_info.get("server_name") or server_info.get("cluster_name")
                remnawave_checked = True
            elif panel_type == "3x-ui":
                tasks.append(fetch_traffic(server_info, client_id))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for server, result in results:
        user_traffic_data[server] = result

    if remnawave_client_id and remnawave_server_ref:
        profile = await get_remnawave_profile(
            session, str(remnawave_server_ref), remnawave_client_id, fallback_any=True, username=email
        )
        if not profile:
            user_traffic_data["Remnawave (общий)"] = "Данные недоступны"
        else:
            used_gb = profile.get("used_gb")
            user_traffic_data["Remnawave (общий)"] = round(float(used_gb), 2) if used_gb is not None else 0

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
                client_id = await get_key_client_id_by_email_and_server(session, email, cluster_id)
                if not client_id:
                    logger.warning(f"[Remnawave Reset] client_id не найден для {email} на {server_name}")
                    continue

                async def _reset(api):
                    done = await api.reset_user_traffic(client_id, username=email)
                    if done:
                        await invalidate_remnawave_profile(
                            session,
                            str(server_name or cluster_id),
                            str(client_id),
                            fallback_any=True,
                        )
                    return done

                tasks.append(with_remnawave_api(session, server_name or cluster_id, _reset, fallback_any=True))
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
