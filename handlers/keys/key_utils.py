import asyncio

from typing import Any

import asyncpg

from py3xui import AsyncApi

from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL, LIMIT_IP, PUBLIC_LINK, SUPERNODE, TOTAL_GB, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
from database import get_servers, store_key, delete_notification
from handlers.utils import get_least_loaded_cluster
from logger import logger
from panels.remnawave import RemnawaveAPI
from panels.three_xui import (
    ClientConfig,
    add_client,
    delete_client,
    extend_client_key,
    get_client_traffic,
    toggle_client,
)

from datetime import datetime, timezone


async def create_key_on_cluster(
    cluster_id: str,
    tg_id: int,
    client_id: str,
    email: str,
    expiry_timestamp: int,
    plan: int = None,
    session=None,
):
    try:
        servers = await get_servers()
        cluster = servers.get(cluster_id)
        server_id_to_store = cluster_id

        if not cluster:
            found_servers = []
            for _key, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        found_servers.append(server_info)
            if found_servers:
                cluster = found_servers
                server_id_to_store = found_servers[0].get("server_name")
            else:
                raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")

        semaphore = asyncio.Semaphore(2)

        remnawave_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"]
        xui_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "3x-ui"]

        remnawave_created = False
        remnawave_key = None
        remnawave_client_id = None

        if remnawave_servers:
            remna = RemnawaveAPI(remnawave_servers[0]["api_url"])
            logged_in = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
            if not logged_in:
                logger.error("Не удалось войти в Remnawave API")
            else:
                expire_at = datetime.utcfromtimestamp(expiry_timestamp / 1000).isoformat() + "Z"
                inbound_ids = [s.get("inbound_id") for s in remnawave_servers if s.get("inbound_id")]

                if not inbound_ids:
                    logger.warning("Нет inbound_id у серверов Remnawave")
                else:
                    traffic_limit_bytes = int((plan or 1) * TOTAL_GB * 1024**3)

                    user_data = {
                        "username": email,
                        "trafficLimitStrategy": "NO_RESET",
                        "trafficLimitBytes": traffic_limit_bytes,
                        "expireAt": expire_at,
                        "telegramId": tg_id,
                        "activeUserInbounds": inbound_ids,
                    }

                    result = await remna.create_user(user_data)
                    if not result:
                        logger.error("Ошибка при создании пользователя в Remnawave")
                    else:
                        remnawave_created = True
                        remnawave_key = result.get("subscriptionUrl")
                        remnawave_client_id = result.get("uuid")
                        logger.info(f"[Key Creation] Пользователь создан в Remnawave: {result}")

        public_link = f"{PUBLIC_LINK}{email}/{tg_id}" if xui_servers else None
        final_client_id = remnawave_client_id or client_id

        if xui_servers:
            if SUPERNODE:
                for server_info in xui_servers:
                    await create_client_on_server(
                        server_info,
                        tg_id,
                        final_client_id,
                        email,
                        expiry_timestamp,
                        semaphore,
                        plan=plan,
                    )
            else:
                await asyncio.gather(
                    *(
                        create_client_on_server(
                            server,
                            tg_id,
                            final_client_id,
                            email,
                            expiry_timestamp,
                            semaphore,
                            plan=plan,
                        )
                        for server in xui_servers
                    ),
                    return_exceptions=True,
                )

        if (remnawave_created and remnawave_client_id) or xui_servers:
            await store_key(
                tg_id,
                final_client_id,
                email,
                expiry_timestamp,
                key=public_link,
                server_id=server_id_to_store,
                session=session,
                remnawave_link=remnawave_key,
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
            logger=logger,
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

        total_gb_value = int((plan or 1) * TOTAL_GB * 1024**3)

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

        async with asyncpg.create_pool(DATABASE_URL) as pool:
            async with pool.acquire() as conn:
                tg_id_query = "SELECT tg_id FROM keys WHERE client_id = $1 LIMIT 1"
                tg_id_record = await conn.fetchrow(tg_id_query, client_id)

                if not tg_id_record:
                    logger.error(f"Не найден пользователь с client_id={client_id} в таблице keys.")
                    return False

                tg_id = tg_id_record["tg_id"]

                notification_prefixes = ["key_24h", "key_10h", "key_expired", "renew"]
                for notif in notification_prefixes:
                    notification_id = f"{email}_{notif}"
                    await delete_notification(tg_id, notification_id, session=conn)
                logger.info(f"🧹 Уведомления для ключа {email} очищены при продлении.")

        remnawave_inbound_ids = []
        tasks = []
        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")

            if panel_type == "remnawave":
                inbound_uuid = server_info.get("inbound_id")
                if inbound_uuid:
                    remnawave_inbound_ids.append(inbound_uuid)
                else:
                    logger.warning(f"Не указан inbound_id для продления Remnawave на сервере {server_name}")

        if remnawave_inbound_ids:
            remnawave_server = next(
                (srv for srv in cluster if srv.get("panel_type", "").lower() == "remnawave" and srv.get("inbound_id") in remnawave_inbound_ids),
                None
            )

            if not remnawave_server:
                logger.error("❌ Не найден Remnawave сервер для продления")
            else:
                remna = RemnawaveAPI(remnawave_server["api_url"])
                logged_in = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                if logged_in:
                    expire_iso = datetime.utcfromtimestamp(new_expiry_time // 1000).isoformat() + "Z"
                    updated = await remna.update_user(
                        uuid=client_id,
                        expire_at=expire_iso,
                        active_user_inbounds=remnawave_inbound_ids,
                        traffic_limit_bytes=total_gb
                    )
                    if updated:
                        logger.info(f"Подписка Remnawave {client_id} успешно продлена")
                    else:
                        logger.warning(f"Не удалось продлить подписку Remnawave {client_id}")
                else:
                    logger.error("Не удалось войти в Remnawave API")

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")

            if panel_type == "3x-ui":
                xui = AsyncApi(
                    server_info["api_url"],
                    username=ADMIN_USERNAME,
                    password=ADMIN_PASSWORD,
                    logger=logger,
                )

                inbound_id = server_info.get("inbound_id")

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
                    extend_client_key(
                        xui, int(inbound_id), unique_email, new_expiry_time, client_id, total_gb, sub_id, tg_id
                    )
                )

            elif panel_type != "remnawave":
                logger.warning(f"Неизвестный тип панели '{panel_type}' для сервера {server_name}")

        await asyncio.gather(*tasks, return_exceptions=True)

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

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()

            if panel_type == "remnawave":
                remna = RemnawaveAPI(server_info["api_url"])
                logged_in = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                if not logged_in:
                    logger.error("Не удалось войти в Remnawave API")
                    continue

                success = await remna.delete_user(client_id)
                if success:
                    logger.info(f"Клиент Remnawave {client_id} успешно удалён")
                else:
                    logger.warning(f"Не удалось удалить клиента Remnawave {client_id}")
                continue

            elif panel_type == "3x-ui":
                xui = AsyncApi(
                    server_info["api_url"],
                    username=ADMIN_USERNAME,
                    password=ADMIN_PASSWORD,
                    logger=logger,
                )

                inbound_id = server_info.get("inbound_id")
                if not inbound_id:
                    logger.warning(
                        f"INBOUND_ID отсутствует для сервера {server_info.get('server_name', 'unknown')}. Пропуск."
                    )
                    continue

                await delete_client(
                    xui,
                    int(inbound_id),
                    email,
                    client_id,
                )

            else:
                logger.warning(f"Неизвестный тип панели '{panel_type}' для сервера {server_info.get('server_name')}")

    except Exception as e:
        logger.error(f"Не удалось удалить ключ {client_id} в кластере/на сервере {cluster_id}: {e}")
        raise e


async def update_key_on_cluster(tg_id, client_id, email, expiry_time, cluster_id):
    """
    Пересоздаёт ключ на всех серверах указанного кластера (или сервера, если передано имя).
    Работает с панелями 3x-ui и Remnawave. Использует новый client_id от Remnawave для всех серверов (если пересоздан).
    Если SUPERNODE активен — email делается уникальным на каждый сервер.
    Возвращает кортеж: (новый client_id, remnawave ссылка или None).
    """
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

        expire_iso = datetime.utcfromtimestamp(expiry_time / 1000).replace(tzinfo=timezone.utc).isoformat()
        tasks = []

        remnawave_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"]
        xui_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "3x-ui"]

        remnawave_client_id = None
        remnawave_key = None

        if remnawave_servers:
            inbound_ids = [s["inbound_id"] for s in remnawave_servers if s.get("inbound_id")]

            remna = RemnawaveAPI(remnawave_servers[0]["api_url"])
            logged_in = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
            if logged_in:
                await remna.delete_user(client_id)

                user_data = {
                    "username": email,
                    "trafficLimitStrategy": "NO_RESET",
                    "expireAt": expire_iso,
                    "telegramId": tg_id,
                    "activeUserInbounds": inbound_ids,
                }

                result = await remna.create_user(user_data)
                if result:
                    remnawave_client_id = result.get("uuid")
                    remnawave_key = result.get("subscriptionUrl")
                    logger.info(f"[Update] Remnawave: клиент заново создан, новый UUID: {remnawave_client_id}")
                else:
                    logger.error(f"[Update] Ошибка создания Remnawave клиента")
            else:
                logger.error(f"[Update] Не удалось авторизоваться в Remnawave")

        if not remnawave_client_id:
            logger.warning(f"[Update] Remnawave client_id не получен. Используется исходный: {client_id}")
            remnawave_client_id = client_id

        for server_info in xui_servers:
            server_name = server_info.get("server_name", "unknown")
            inbound_id = server_info.get("inbound_id")
            if not inbound_id:
                logger.warning(f"[Update] INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                continue

            xui = AsyncApi(
                server_info["api_url"],
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                logger=logger,
            )

            if SUPERNODE:
                sub_id = email
                unique_email = f"{email}_{server_name.lower()}"
            else:
                sub_id = email
                unique_email = email

            config = ClientConfig(
                client_id=remnawave_client_id,
                email=unique_email,
                tg_id=tg_id,
                limit_ip=LIMIT_IP,
                total_gb=TOTAL_GB,
                expiry_time=expiry_time,
                enable=True,
                flow="xtls-rprx-vision",
                inbound_id=int(inbound_id),
                sub_id=sub_id,
            )

            tasks.append(add_client(xui, config))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"[Update] Ключ {remnawave_client_id} обновлён на всех серверах кластера {cluster_id}")
        return remnawave_client_id, remnawave_key

    except Exception as e:
        logger.error(f"[Update Error] Ошибка при обновлении ключа {client_id} на {cluster_id}: {e}")
        raise e


async def update_subscription(tg_id: int, email: str, session: Any, cluster_override: str = None) -> None:
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
    old_cluster_id = record["server_id"]
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

    await delete_key_from_cluster(old_cluster_id, email, client_id)

    await session.execute(
        "DELETE FROM keys WHERE tg_id = $1 AND email = $2",
        tg_id,
        email,
    )
    new_cluster_id = cluster_override or await get_least_loaded_cluster()

    new_client_id, remnawave_key = await update_key_on_cluster(
        tg_id, client_id, email, expiry_time, new_cluster_id
    )

    servers = await get_servers()
    cluster_servers = servers.get(new_cluster_id, [])
    has_xui = any(s.get("panel_type", "").lower() == "3x-ui" for s in cluster_servers)

    final_key_link = public_link if has_xui else None

    await store_key(
        tg_id,
        new_client_id,
        email,
        expiry_time,
        key=final_key_link,
        remnawave_link=remnawave_key,
        server_id=new_cluster_id,
        session=session,
    )


async def get_user_traffic(session: Any, tg_id: int, email: str) -> dict[str, Any]:
    """
    Получает трафик пользователя на всех серверах, где у него есть ключ (3x-ui и Remnawave).

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
        return {"status": "error", "message": "У пользователя нет активных ключей."}

    server_ids = {row["server_id"] for row in rows}

    query_servers = """
        SELECT server_name, cluster_name, api_url, panel_type
        FROM servers 
        WHERE server_name = ANY($1) OR cluster_name = ANY($1)
    """
    server_rows = await session.fetch(query_servers, list(server_ids))

    if not server_rows:
        logger.error(f"Не найдено серверов для: {server_ids}")
        return {"status": "error", "message": f"Серверы не найдены: {', '.join(server_ids)}"}

    servers_map = {row["server_name"]: row for row in server_rows}

    user_traffic_data = {}

    async def fetch_traffic(server_info: dict, client_id: str) -> tuple[str, Any]:
        server_name = server_info["server_name"]
        api_url = server_info["api_url"]
        panel_type = server_info.get("panel_type", "3x-ui").lower()

        try:
            if panel_type == "3x-ui":
                xui = AsyncApi(api_url, username=ADMIN_USERNAME, password=ADMIN_PASSWORD, logger=logger)
                await xui.login()
                traffic_info = await get_client_traffic(xui, client_id)
                if traffic_info["status"] == "success" and traffic_info["traffic"]:
                    client_data = traffic_info["traffic"][0]
                    used_gb = (client_data.up + client_data.down) / 1073741824
                    return server_name, round(used_gb, 2)
                else:
                    return server_name, "Ошибка получения трафика"

            elif panel_type == "remnawave":
                remna = RemnawaveAPI(api_url)
                logged_in = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                if not logged_in:
                    return server_name, "Не удалось авторизоваться"

                user_data = await remna.get_user_by_uuid(client_id)
                if not user_data:
                    return server_name, "Клиент не найден"

                used_bytes = user_data.get("usedTrafficBytes", 0)
                used_gb = used_bytes / 1073741824
                return server_name, round(used_gb, 2)

            else:
                return server_name, f"Неизвестная панель: {panel_type}"

        except Exception as e:
            return server_name, f"Ошибка: {e}"

    tasks = []
    for row in rows:
        client_id = row["client_id"]
        server_id = row["server_id"]

        matched_servers = [s for s in servers_map.values() if s["server_name"] == server_id or s["cluster_name"] == server_id]
        for server_info in matched_servers:
            tasks.append(fetch_traffic(server_info, client_id))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for server, result in results:
        user_traffic_data[server] = result

    return {"status": "success", "traffic": user_traffic_data}


async def toggle_client_on_cluster(cluster_id: str, email: str, client_id: str, enable: bool = True) -> dict[str, Any]:
    """
    Включает или отключает клиента на всех серверах указанного кластера.

    Args:
        cluster_id (str): ID кластера или имя сервера
        email (str): Email клиента
        client_id (str): UUID клиента
        enable (bool): True для включения, False для отключения

    Returns:
        dict[str, Any]: Результат операции с информацией по каждому серверу
    """
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

        results = {}
        tasks = []

        for server_info in cluster:
            xui = AsyncApi(
                server_info["api_url"],
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                logger=logger,
            )

            inbound_id = server_info.get("inbound_id")
            server_name = server_info.get("server_name", "unknown")

            if not inbound_id:
                logger.warning(f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                results[server_name] = False
                continue

            if SUPERNODE:
                unique_email = f"{email}_{server_name.lower()}"
            else:
                unique_email = email

            tasks.append(toggle_client(xui, int(inbound_id), unique_email, client_id, enable))

        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for server_info, result in zip(cluster, task_results, strict=False):
            server_name = server_info.get("server_name", "unknown")
            if isinstance(result, Exception):
                logger.error(f"Ошибка на сервере {server_name}: {result}")
                results[server_name] = False
            else:
                results[server_name] = result

        status = "включен" if enable else "отключен"
        logger.info(f"Клиент {email} {status} на серверах кластера {cluster_id}")

        return {"status": "success" if any(results.values()) else "error", "results": results}

    except Exception as e:
        logger.error(f"Ошибка при изменении состояния клиента {email} в кластере {cluster_id}: {e}")
        return {"status": "error", "error": str(e)}


async def reset_traffic_in_cluster(cluster_id: str, email: str) -> None:
    """
    Сбрасывает трафик клиента на всех серверах указанного кластера (или конкретного сервера).
    Работает с 3x-ui и Remnawave.

    Args:
        cluster_id (str): ID кластера или имя сервера
        email (str): Email клиента (будет преобразован в уникальный для SUPERNODE)
    """
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
        remnawave_done = False

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")
            api_url = server_info.get("api_url")
            inbound_id = server_info.get("inbound_id")

            if panel_type == "remnawave" and not remnawave_done:
                conn = await asyncpg.connect(DATABASE_URL)
                try:
                    row = await conn.fetchrow(
                        "SELECT client_id FROM keys WHERE email = $1 AND server_id = $2 LIMIT 1",
                        email,
                        cluster_id,
                    )
                finally:
                    await conn.close()

                if not row:
                    logger.warning(f"[Remnawave Reset] client_id не найден для {email} на {server_name}")
                    continue

                client_id = row["client_id"]

                remna = RemnawaveAPI(api_url)
                logged_in = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                if not logged_in:
                    logger.warning(f"[Reset Traffic] Не удалось авторизоваться в Remnawave ({server_name})")
                    continue

                tasks.append(remna.reset_user_traffic(client_id))
                remnawave_done = True
                continue

            if panel_type == "3x-ui":
                if not inbound_id:
                    logger.warning(f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                    continue

                xui = AsyncApi(api_url, username=ADMIN_USERNAME, password=ADMIN_PASSWORD, logger=logger)
                await xui.login()

                unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email
                tasks.append(xui.client.reset_stats(int(inbound_id), unique_email))
            else:
                logger.warning(f"[Reset Traffic] Неизвестный тип панели '{panel_type}' на {server_name}")

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"[Reset Traffic] Трафик клиента {email} успешно сброшен в кластере {cluster_id}")

    except Exception as e:
        logger.error(f"[Reset Traffic] Ошибка при сбросе трафика клиента {email} в кластере {cluster_id}: {e}")
        raise