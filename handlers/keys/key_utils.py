import asyncio
from typing import Any

from py3xui import AsyncApi

from client import ClientConfig, add_client, delete_client, extend_client_key, get_client_traffic
from config import ADMIN_PASSWORD, ADMIN_USERNAME, LIMIT_IP, PUBLIC_LINK, SUPERNODE, TOTAL_GB, USE_COUNTRY_SELECTION
from database import get_servers, store_key
from handlers.utils import get_least_loaded_cluster
from logger import logger


async def create_key_on_cluster(
    cluster_id: str, tg_id: int, client_id: str, email: str, expiry_timestamp: int, plan: int = None
):
    """
    Создает ключ на всех серверах указанного кластера.
    """
    try:
        servers = await get_servers()
        cluster = servers.get(cluster_id)

        if not cluster:
            raise ValueError(f"Кластер с ID {cluster_id} не найден.")

        semaphore = asyncio.Semaphore(2)

        if SUPERNODE:
            for server_info in cluster:
                await create_client_on_server(
                    server_info, tg_id, client_id, email, expiry_timestamp, semaphore, plan=plan
                )
        else:
            await asyncio.gather(
                *(
                    create_client_on_server(server, tg_id, client_id, email, expiry_timestamp, semaphore, plan=plan)
                    for server in cluster
                )
            )

    except Exception as e:
        logger.error(f"Ошибка при создании ключа: {e}")
        raise e


async def create_client_on_server(
    server_info: dict,
    tg_id: int,
    client_id: str,
    email: str,
    expiry_timestamp: int,
    semaphore: asyncio.Semaphore,
    plan: int = None,
):
    """
    Создает клиента на указанном сервере.
    """
    async with semaphore:
        xui = AsyncApi(
            server_info["api_url"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )

        inbound_id = server_info.get("inbound_id")
        server_name = server_info.get("server_name", "unknown")

        if not inbound_id:
            logger.warning(f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
            return

        if SUPERNODE:
            unique_email = f"{email}_{server_name.lower()}"
            sub_id = email
        else:
            unique_email = email
            sub_id = unique_email

        total_gb_value = int(TOTAL_GB) if plan is None else int(plan) * int(TOTAL_GB)

        await add_client(
            xui,
            ClientConfig(
                client_id=client_id,
                email=unique_email,
                tg_id=tg_id,
                limit_ip=LIMIT_IP,
                total_gb=total_gb_value,
                expiry_time=expiry_timestamp,
                enable=True,
                flow="xtls-rprx-vision",
                inbound_id=int(inbound_id),
                sub_id=sub_id,
            ),
        )

        if SUPERNODE:
            await asyncio.sleep(0.7)


async def renew_key_in_cluster(cluster_id, email, client_id, new_expiry_time, total_gb):
    try:
        servers = await get_servers()
        cluster = servers.get(cluster_id)

        if not cluster:
            found_servers = []
            for _key, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        found_servers.append(server_info)
            if found_servers:
                cluster = found_servers
            else:
                raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")

        tasks = []
        for server_info in cluster:
            xui = AsyncApi(
                server_info["api_url"],
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
            )

            inbound_id = server_info.get("inbound_id")
            server_name = server_info.get("server_name", "unknown")

            if not inbound_id:
                logger.warning(f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                continue

            if SUPERNODE:
                unique_email = f"{email}_{server_name.lower()}"
                sub_id = email
            else:
                unique_email = email
                sub_id = unique_email

            tasks.append(
                extend_client_key(xui, int(inbound_id), unique_email, new_expiry_time, client_id, total_gb, sub_id)
            )

        await asyncio.gather(*tasks)

    except Exception as e:
        logger.error(f"Не удалось продлить ключ {client_id} в кластере/на сервере {cluster_id}: {e}")
        raise e


async def delete_key_from_cluster(cluster_id, email, client_id):
    """Удаление ключа с серверов в кластере или с конкретного сервера"""
    try:
        servers = await get_servers()
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
        for server_info in cluster:
            xui = AsyncApi(
                server_info["api_url"],
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
            )

            inbound_id = server_info.get("inbound_id")
            if not inbound_id:
                logger.warning(
                    f"INBOUND_ID отсутствует для сервера {server_info.get('server_name', 'unknown')}. Пропуск."
                )
                continue

            tasks.append(
                delete_client(
                    xui,
                    int(inbound_id),
                    email,
                    client_id,
                )
            )

        await asyncio.gather(*tasks)

    except Exception as e:
        logger.error(f"Не удалось удалить ключ {client_id} в кластере/на сервере {cluster_id}: {e}")
        raise e


async def update_key_on_cluster(tg_id, client_id, email, expiry_time, cluster_id):
    try:
        servers = await get_servers()
        cluster = servers.get(cluster_id)

        if not cluster:
            raise ValueError(f"Кластер с ID {cluster_id} не найден.")

        tasks = []
        for server_info in cluster:
            xui = AsyncApi(
                server_info["api_url"],
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
            )

            inbound_id = server_info.get("inbound_id")
            if not inbound_id:
                logger.warning(
                    f"INBOUND_ID отсутствует для сервера {server_info.get('server_name', 'unknown')}. Пропуск."
                )
                continue

            tasks.append(
                add_client(
                    xui,
                    ClientConfig(
                        client_id=client_id,
                        email=email,
                        tg_id=tg_id,
                        limit_ip=LIMIT_IP,
                        total_gb=TOTAL_GB,
                        expiry_time=expiry_time,
                        enable=True,
                        flow="xtls-rprx-vision",
                        inbound_id=int(inbound_id),
                        sub_id=email,
                    ),
                )
            )

        await asyncio.gather(*tasks)

        logger.info(f"Ключ успешно обновлен для {client_id} на всех серверах в кластере {cluster_id}")

    except Exception as e:
        logger.error(f"Ошибка при обновлении ключа на серверах кластера {cluster_id} для {client_id}: {e}")
        raise e


async def update_subscription(tg_id: int, email: str, session: Any) -> None:
    record = await session.fetchrow(
        """
        SELECT k.key, k.expiry_time, k.email, k.server_id, k.client_id
        FROM keys k
        WHERE k.tg_id = $1 AND k.email = $2
        """,
        tg_id,
        email,
    )

    if not record:
        raise ValueError(f"The key {email} does not exist in database")

    expiry_time = record["expiry_time"]
    client_id = record["client_id"]
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

    await session.execute(
        """
        DELETE FROM keys
        WHERE tg_id = $1 AND email = $2
        """,
        tg_id,
        email,
    )

    least_loaded_cluster_id = await get_least_loaded_cluster()

    await asyncio.gather(
        update_key_on_cluster(
            tg_id,
            client_id,
            email,
            expiry_time,
            least_loaded_cluster_id,
        )
    )

    await store_key(
        tg_id,
        client_id,
        email,
        expiry_time,
        public_link,
        server_id=least_loaded_cluster_id,
        session=session,
    )


async def get_user_traffic(session: Any, tg_id: int, email: str) -> dict[str, Any]:
    """
    Получает трафик пользователя на всех серверах, где у него есть ключ.

    Args:
        session (Any): Сессия базы данных.
        tg_id (int): ID пользователя Telegram.
        email (str): Email пользователя.

    Returns:
        dict[str, Any]: Структура с данными о трафике.
    """

    query = "SELECT client_id, server_id FROM keys WHERE tg_id = $1 AND email = $2"
    rows = await session.fetch(query, tg_id, email)

    if not rows:
        return {"status": "error", "message": "❌ У пользователя нет активных ключей."}

    server_ids = {row["server_id"] for row in rows}

    query_servers = """
        SELECT server_name, api_url FROM servers 
        WHERE server_name = ANY($1) OR cluster_name = ANY($1)
    """
    server_rows = await session.fetch(query_servers, list(server_ids))

    if not server_rows:
        logger.error(f"❌ Не найдено серверов для: {server_ids}")
        return {"status": "error", "message": f"❌ Серверы не найдены: {', '.join(server_ids)}"}

    servers_map = {row["server_name"]: row["api_url"] for row in server_rows}

    user_traffic_data = {}

    async def fetch_traffic(api_url: str, client_id: str, server: str) -> tuple[str, Any]:
        """
        Получает трафик с сервера для заданного client_id.
        Возвращает кортеж: (server, used_gb) или (server, ошибка).
        """
        xui = AsyncApi(api_url, username=ADMIN_USERNAME, password=ADMIN_PASSWORD)
        try:
            traffic_info = await get_client_traffic(xui, client_id)
            if traffic_info["status"] == "success" and traffic_info["traffic"]:
                client_data = traffic_info["traffic"][0]
                used_gb = (client_data.up + client_data.down) / 1073741824
                return server, round(used_gb, 2)
            else:
                return server, "Ошибка получения трафика"
        except Exception as e:
            return server, f"Ошибка: {e}"

    tasks = []
    for row in rows:
        client_id = row["client_id"]
        server_id = row["server_id"]
        if server_id in servers_map:
            api_url = servers_map[server_id]
            tasks.append(fetch_traffic(api_url, client_id, server_id))
        else:
            for server, api_url in servers_map.items():
                tasks.append(fetch_traffic(api_url, client_id, server))

    results = await asyncio.gather(*tasks)
    for server, result in results:
        user_traffic_data[server] = result

    return {"status": "success", "traffic": user_traffic_data}
