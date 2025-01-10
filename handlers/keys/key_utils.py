import asyncio

from config import ADMIN_PASSWORD, ADMIN_USERNAME, LIMIT_IP, TOTAL_GB
from py3xui import AsyncApi

from client import add_client, delete_client, extend_client_key
from database import get_servers_from_db
from logger import logger


async def create_key_on_cluster(cluster_id, tg_id, client_id, email, expiry_timestamp):
    """
    Создает ключ на всех серверах указанного кластера.

    :param cluster_id: ID кластера.
    :param tg_id: Telegram ID пользователя.
    :param client_id: Уникальный идентификатор клиента.
    :param email: Email клиента.
    :param expiry_timestamp: Время истечения ключа (timestamp в миллисекундах).
    :param allow_existing: Игнорируется, ключи всегда продолжают выполнение.
    """
    try:
        tasks = []
        servers = await get_servers_from_db()
        cluster = servers.get(cluster_id)

        if not cluster:
            raise ValueError(f"Кластер с ID {cluster_id} не найден.")

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
                    client_id,
                    email,
                    tg_id,
                    limit_ip=LIMIT_IP,
                    total_gb=TOTAL_GB,
                    expiry_time=expiry_timestamp,
                    enable=True,
                    flow="xtls-rprx-vision",
                    inbound_id=int(inbound_id),
                )
            )

        await asyncio.gather(*tasks)

    except Exception as e:
        logger.error(f"Ошибка при создании ключа: {e}")
        raise e


async def renew_key_in_cluster(cluster_id, email, client_id, new_expiry_time, total_gb):
    try:
        servers = await get_servers_from_db()
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
                extend_client_key(
                    xui,
                    int(inbound_id),
                    email,
                    new_expiry_time,
                    client_id,
                    total_gb,
                )
            )

        await asyncio.gather(*tasks)

    except Exception as e:
        logger.error(
            f"Не удалось продлить ключ {client_id} в кластере {cluster_id}: {e}"
        )
        raise e


async def delete_key_from_db(client_id, session):
    try:
        await session.execute("DELETE FROM keys WHERE client_id = $1", client_id)
    except Exception as e:
        logger.error(f"Ошибка при удалении ключа {client_id} из базы данных: {e}")


async def delete_key_from_cluster(cluster_id, email, client_id):
    """Удаление ключа с серверов в кластере"""
    try:
        servers = await get_servers_from_db()
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
                delete_client(
                    xui,
                    int(inbound_id),
                    email,
                    client_id,
                )
            )

        await asyncio.gather(*tasks)

    except Exception as e:
        logger.error(
            f"Не удалось удалить ключ {client_id} в кластере {cluster_id}: {e}"
        )
        raise e


async def update_key_on_cluster(tg_id, client_id, email, expiry_time, cluster_id):
    try:
        servers = await get_servers_from_db()
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
                    client_id,
                    email,
                    tg_id,
                    limit_ip=LIMIT_IP,
                    total_gb=TOTAL_GB,
                    expiry_time=expiry_time,
                    enable=True,
                    flow="xtls-rprx-vision",
                    inbound_id=int(inbound_id),
                )
            )

        await asyncio.gather(*tasks)

        logger.info(
            f"Ключ успешно обновлен для {client_id} на всех серверах в кластере {cluster_id}"
        )

    except Exception as e:
        logger.error(
            f"Ошибка при обновлении ключа на серверах кластера {cluster_id} для {client_id}: {e}"
        )
        raise e
