import random
import re

import asyncpg
from config import DATABASE_URL

from bot import bot
from database import get_servers_from_db
from logger import logger


def sanitize_key_name(key_name: str) -> str:
    """
    Очищает название ключа, оставляя только допустимые символы.

    Args:
        key_name (str): Исходное название ключа.

    Returns:
        str: Очищенное название ключа в нижнем регистре.
    """
    return re.sub(r"[^a-z0-9@._-]", "", key_name.lower())


def generate_random_email(length: int = 6) -> str:
    """
    Генерирует случайный email с заданной длиной.

    Args:
        length (int, optional): Длина случайной строки. По умолчанию 6.

    Returns:
        str: Сгенерированная случайная строка.
    """
    return "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=length))


async def get_least_loaded_cluster() -> str:
    """
    Определяет кластер с наименьшей загрузкой.

    Returns:
        str: Идентификатор наименее загруженного кластера.
    """
    servers = await get_servers_from_db()

    cluster_loads: dict[str, int] = {cluster_id: 0 for cluster_id in servers.keys()}

    async with asyncpg.create_pool(DATABASE_URL) as pool:
        async with pool.acquire() as conn:
            keys = await conn.fetch("SELECT server_id FROM keys")
            for key in keys:
                cluster_id = key["server_id"]
                if cluster_id in cluster_loads:
                    cluster_loads[cluster_id] += 1

    logger.info(f"Cluster loads after database query: {cluster_loads}")

    if not cluster_loads:
        logger.warning("No clusters found in database or configuration.")
        return "cluster1"

    least_loaded_cluster = min(cluster_loads, key=lambda k: (cluster_loads[k], k))

    logger.info(f"Least loaded cluster selected: {least_loaded_cluster}")

    return least_loaded_cluster


async def handle_error(
    tg_id: int, callback_query: object | None = None, message: str = ""
) -> None:
    """
    Обрабатывает ошибку, отправляя сообщение пользователю.

    Args:
        tg_id (int): Идентификатор пользователя в Telegram.
        callback_query (Optional[object], optional): Объект запроса обратного вызова. По умолчанию None.
        message (str, optional): Текст сообщения об ошибке. По умолчанию пустая строка.
    """
    try:
        if callback_query and hasattr(callback_query, "message"):
            try:
                await bot.delete_message(
                    chat_id=tg_id, message_id=callback_query.message.message_id
                )
            except Exception as delete_error:
                logger.warning(f"Не удалось удалить сообщение: {delete_error}")

        await bot.send_message(tg_id, message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка при обработке ошибки: {e}")
