import asyncpg
from loguru import logger

from auth import login_with_credentials
from client import add_client, delete_client, extend_client_key, reset_client_traffic
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL, RESET_TRAFFIC


async def create_key_on_server(server_id, tg_id, client_id, email, expiry_timestamp):
    try:
        session = await login_with_credentials(
            server_id, ADMIN_USERNAME, ADMIN_PASSWORD
        )
        response = await add_client(
            session,
            server_id,
            client_id,
            email,
            tg_id,
            limit_ip=1,
            total_gb=0,
            expiry_time=expiry_timestamp,
            enable=True,
            flow="xtls-rprx-vision",
        )
        if not response.get("success", True):
            error_msg = response.get("msg", "Неизвестная ошибка.")
            if "Duplicate email" in error_msg:
                raise ValueError(
                    f"Имя {email} уже занято на сервере {server_id}")
            else:
                raise Exception(error_msg)
    except Exception as e:
        logger.error(f"Ошибка на сервере {server_id}: {e}")


async def renew_server_key(
        server_id,
        tg_id, client_id,
        email,
        new_expiry_time,
        reset_traffic=RESET_TRAFFIC
):
    try:
        session = await login_with_credentials(
            server_id, ADMIN_USERNAME, ADMIN_PASSWORD
        )

        await extend_client_key(
            session, server_id, tg_id, client_id, email, new_expiry_time
        )

        if reset_traffic:
            await reset_client_traffic(session, server_id, email)

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
        logger.error(
            f"Ошибка при удалении ключа {client_id} из базы данных: {e}")
    finally:
        await conn.close()


async def delete_key_from_server(server_id, client_id):
    """Удаление ключа с сервера"""
    try:
        async with login_with_credentials(server_id,
                                          ADMIN_USERNAME,
                                          ADMIN_PASSWORD) as session:
            success = await delete_client(session, server_id, client_id)

            if not success:
                logger.error(
                    f"Ошибка удаления ключа {client_id} на сервере {server_id}")
    except Exception as e:
        logger.error(
            f"Ошибка при удалении ключа {client_id} с сервера {server_id}: {e}")


async def update_key_on_server(tg_id, client_id, email, expiry_time, server_id):
    try:
        session = await login_with_credentials(
            server_id, ADMIN_USERNAME, ADMIN_PASSWORD
        )
        response = await add_client(
            session,
            server_id,
            client_id,
            email,
            tg_id,
            limit_ip=1,
            total_gb=0,
            expiry_time=expiry_time,
            enable=True,
            flow="xtls-rprx-vision",
        )

        if not response.get("success"):
            logger.error(
                f"Ошибка при обновлении ключа на сервере {server_id} для {client_id}"
            )
        else:
            logger.info(
                f"Ключ успешно обновлен на сервере {server_id} для {client_id}")

    except Exception as e:
        logger.error(
            f"Ошибка при обновлении ключа на сервере {server_id} для {client_id}: {e}"
        )
