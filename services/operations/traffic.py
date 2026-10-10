import asyncio

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_key_details, get_servers
from database.access.resolution import UserId, resolve_user_optional
from database.keys import (
    get_key_client_id_by_email_and_server,
    get_user_keys_with_servers_by_email,
    lock_owned_key_for_operation,
    resolve_key_operation_owner,
)
from database.traffic_notifications import reset_traffic_notification_state
from logger import logger
from panels._3xui import get_client_traffic, get_xui_instance, reset_client_traffic
from panels.remnawave_runtime import (
    get_remnawave_profile,
    invalidate_remnawave_profile,
    with_remnawave_api,
)
from settings.config import SUPERNODE
from settings.texts import TRAFFIC_RESET_FAILED
from utils.traffic_resources import traffic_resource_id


async def get_user_traffic(session: AsyncSession, tg_id: int, email: str) -> dict[str, Any]:
    """Получает трафик клиента на серверах его ключей."""
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
                unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email
                traffic_info = await get_client_traffic(xui, client_id, unique_email)
                if traffic_info["status"] == "success":
                    used_gb = int(traffic_info.get("used_bytes") or 0) / 1073741824
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
        key = await get_key_details(session, email)
        subscription_url = None
        if key and key.get("user_id") == u.id and key.get("client_id") == remnawave_client_id:
            subscription_url = key.get("remnawave_link") or key.get("link")
        profile = await get_remnawave_profile(
            session,
            str(remnawave_server_ref),
            remnawave_client_id,
            fallback_any=True,
            username=email,
            subscription_url=subscription_url,
        )
        if not profile:
            user_traffic_data["Remnawave (общий)"] = "Данные недоступны"
        else:
            used_gb = profile.get("used_gb")
            user_traffic_data["Remnawave (общий)"] = round(float(used_gb), 2) if used_gb is not None else 0

    return {"status": "success", "traffic": user_traffic_data}


async def _reset_xui_traffic(api_url: str, inbound_id: int, email: str, client_id: str):
    api = await get_xui_instance(api_url)
    return await reset_client_traffic(api, inbound_id, email, client_id)


async def reset_traffic_in_cluster(cluster_id: str, email: str, session: AsyncSession) -> dict:
    """Сбрасывает трафик и возвращает подтверждённые результаты панелей."""
    from middlewares.session import operation_session

    async with operation_session(session) as current:
        if current is not session:
            return await reset_traffic_in_cluster(cluster_id, email, current)
    try:
        key = await get_key_details(session, email)
        if not key:
            raise ValueError("Ключ для сброса трафика не найден")
        client_id = key["client_id"]
        user_id = UserId(key["user_id"])
        if await lock_owned_key_for_operation(session, user_id, client_id, email) is None:
            raise ValueError("Владелец ключа изменился перед сбросом трафика")
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
        resources = []
        remnawave_done = set()
        skipped_servers = set()

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")
            api_url = server_info.get("api_url")
            inbound_id = server_info.get("inbound_id")

            if panel_type == "remnawave":
                if api_url in remnawave_done:
                    continue
                scoped_client_id = await get_key_client_id_by_email_and_server(session, email, cluster_id)
                if scoped_client_id != client_id:
                    logger.warning(f"[Remnawave Reset] client_id не найден для {email} на {server_name}")
                    skipped_servers.add(server_name)
                    continue

                async def _reset(api):
                    return await api.reset_user_traffic(client_id, username=email)

                tasks.append(
                    with_remnawave_api(
                        session,
                        server_name or cluster_id,
                        _reset,
                        fallback_any=False,
                        client_id=client_id,
                        username=email,
                        subscription_url=key.get("remnawave_link") or key.get("link"),
                    )
                )
                resources.append((panel_type, traffic_resource_id(panel_type, api_url or ""), server_name))
                remnawave_done.add(api_url)
                continue

            if panel_type == "3x-ui":
                if not inbound_id:
                    logger.warning(f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                    skipped_servers.add(server_name)
                    continue

                unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email
                tasks.append(_reset_xui_traffic(api_url, int(inbound_id), unique_email, client_id))
                resources.append((
                    panel_type,
                    traffic_resource_id(panel_type, api_url or "", inbound_id, unique_email),
                    server_name,
                ))
            else:
                logger.warning(f"[Reset Traffic] Неизвестный тип панели '{panel_type}' на {server_name}")
                skipped_servers.add(server_name)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (panel, _resource_id, server_name), result in zip(resources, results, strict=True):
            if panel == "remnawave" and result is True:
                try:
                    await invalidate_remnawave_profile(
                        session,
                        str(server_name or cluster_id),
                        str(client_id),
                        fallback_any=True,
                    )
                except Exception as e:
                    logger.warning(f"[Remnawave Reset] Не удалось сбросить кэш профиля {client_id}: {e}")
        reset_resources = {
            resource_id
            for (panel, resource_id, _server_name), result in zip(resources, results, strict=True)
            if result is True
        }
        if not reset_resources:
            raise ValueError(TRAFFIC_RESET_FAILED)
        owner = await resolve_key_operation_owner(session, user_id, client_id, email)
        if owner is None:
            raise ValueError(TRAFFIC_RESET_FAILED)
        await reset_traffic_notification_state(session, owner, client_id, reset_resources)
        succeeded = sorted({name for _panel, resource, name in resources if resource in reset_resources})
        failed = sorted(
            skipped_servers | {name for _panel, resource, name in resources if resource not in reset_resources}
        )
        if failed:
            logger.warning("[Reset Traffic] Частичный сброс {}: успешно={}, отказ={}", email, succeeded, failed)
        else:
            logger.info(f"[Reset Traffic] Трафик клиента {email} успешно сброшен в кластере {cluster_id}")
        return {"status": "partial" if failed else "success", "succeeded": succeeded, "failed": failed}

    except Exception as e:
        logger.error(f"[Reset Traffic] Ошибка при сбросе трафика клиента {email} в кластере {cluster_id}: {e}")
        raise
