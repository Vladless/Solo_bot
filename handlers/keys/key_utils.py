import asyncpg
from loguru import logger
from py3xui import AsyncApi

from client import add_client, delete_client, extend_client_key
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL, SERVERS


async def create_key_on_server(server_id, tg_id, client_id, email, expiry_timestamp):
    try:
        xui = AsyncApi(
            SERVERS[server_id]["API_URL"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )

        conn = await asyncpg.connect(DATABASE_URL)
        existing_key = await conn.fetchrow("SELECT 1 FROM keys WHERE email = $1", email)

        if existing_key:
            raise ValueError(f"Email {email} уже существует в базе данных.")

        await add_client(
            xui,
            client_id,
            email,
            tg_id,
            limit_ip=1,
            total_gb=0,
            expiry_time=expiry_timestamp,
            enable=True,
            flow="xtls-rprx-vision",
        )

        await conn.close()

    except Exception as e:
        logger.error(f"Ошибка на сервере {server_id}: {e}")
        raise e


async def renew_server_key(server_id, email, client_id, new_expiry_time):
    """
    Функция для продления срока действия ключа на сервере и сброса трафика, если необходимо.
    """
    try:
        xui = AsyncApi(
            SERVERS[server_id]["API_URL"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )

        await extend_client_key(xui, email, new_expiry_time, client_id)

    except Exception as e:
        logger.error(
            f"Не удалось продлить ключ {client_id} и сбросить трафик на сервере {server_id}: {e}"
        )


async def delete_key_from_db(client_id):
    """Удаление ключа из базы данных"""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        await conn.execute("DELETE FROM keys WHERE client_id = $1", client_id)
    except Exception as e:
        logger.error(f"Ошибка при удалении ключа {client_id} из базы данных: {e}")
    finally:
        await conn.close()


async def delete_key_from_server(server_id, email, client_id):
    """Удаление ключа с сервера"""
    try:
        xui = AsyncApi(
            SERVERS[server_id]["API_URL"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )

        await delete_client(xui, email, client_id)

    except Exception as e:
        logger.error(f"Не удалось удалить ключ {client_id} на сервере {server_id}: {e}")


async def update_key_on_server(tg_id, client_id, email, expiry_time, server_id):
    try:
        xui = AsyncApi(
            SERVERS[server_id]["API_URL"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )
        await add_client(
            xui,
            client_id,
            email,
            tg_id,
            limit_ip=1,
            total_gb=0,
            expiry_time=expiry_time,
            enable=True,
            flow="xtls-rprx-vision",
        )

        logger.info(f"Ключ успешно обновлен на сервере {server_id} для {client_id}")

    except Exception as e:
        logger.error(
            f"Ошибка при обновлении ключа на сервере {server_id} для {client_id}: {e}"
        )
