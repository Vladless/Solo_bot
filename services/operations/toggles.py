import asyncio

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database import get_servers
from logger import logger
from panels._3xui import get_xui_instance, toggle_client
from panels.remnawave_runtime import remnawave_api
from settings.config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, REMNAWAVE_TOKEN_LOGIN_ENABLED, SUPERNODE


async def toggle_client_on_cluster(
    cluster_id: str,
    email: str,
    client_id: str,
    enable: bool = True,
    session: AsyncSession = None,
) -> dict[str, Any]:
    """Меняет состояние клиента с подтверждением всех панелей."""
    try:
        if session is None:
            raise ValueError("Не передана сессия для изменения состояния подписки")
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
            raise ValueError(f"Кластер или сервер с ID/именем '{cluster_id}' не найден.")

        async def toggle_one(server):
            name = server.get("server_name", "unknown")
            try:
                if not server.get("api_url"):
                    raise ValueError("Не задан адрес панели")
                panel_type = server.get("panel_type", "3x-ui").lower()
                if panel_type == "3x-ui":
                    inbound_id = int(server.get("inbound_id") or 0)
                    if inbound_id <= 0:
                        raise ValueError("Не задан корректный inbound")
                    xui = await get_xui_instance(server["api_url"])
                    unique_email = f"{email}_{name.lower()}" if SUPERNODE else email
                    confirmed = await toggle_client(xui, inbound_id, unique_email, client_id, enable)
                elif panel_type == "remnawave":
                    async with remnawave_api(server["api_url"]) as api:
                        if not REMNAWAVE_TOKEN_LOGIN_ENABLED and not await api.login(
                            REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
                        ):
                            raise ValueError("Авторизация не удалась")
                        method = api.enable_user if enable else api.disable_user
                        confirmed = await method(client_id, username=email)
                else:
                    raise ValueError(f"Неизвестный тип панели '{panel_type}'")
                return name, confirmed is True
            except Exception as exc:
                logger.warning(f"[Cluster Toggle] {name}: изменение состояния не подтверждено: {exc}")
                return name, False

        confirmations = await asyncio.gather(*(toggle_one(server) for server in cluster))
        results = dict(confirmations)
        success = all(confirmed for _, confirmed in confirmations)
        logger.info(f"[Cluster Toggle] Клиент {email}, enable={enable}, подтверждено={success}: {results}")
        return {"status": "success" if success else "error", "results": results}
    except Exception as exc:
        logger.error(f"[Cluster Toggle] Ошибка изменения состояния клиента {email}: {exc}")
        return {"status": "error", "error": str(exc)}
