import random
import re
from typing import Optional

import asyncpg

from bot import bot
from config import CLUSTERS, DATABASE_URL
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
    cluster_loads: dict[str, int] = {}

    async with asyncpg.create_pool(DATABASE_URL) as pool:
        async with pool.acquire() as conn:
            keys = await conn.fetch("SELECT * FROM keys")

            for key in keys:
                cluster_id = key["server_id"]
                if re.match(r"^cluster\d+$", cluster_id):
                    cluster_loads[cluster_id] = cluster_loads.get(cluster_id, 0) + 1

    logger.info(f"Cluster loads: {cluster_loads}")

    if not cluster_loads:
        available_clusters = [cluster_id for cluster_id in CLUSTERS.keys() if re.match(r"^cluster\d+$", cluster_id)]

        logger.info(f"Available clusters from config: {available_clusters}")

        if available_clusters:
            selected_cluster = available_clusters[0]
            logger.info(f"Returning the first available cluster: {selected_cluster}")
            return selected_cluster

        logger.warning("No valid clusters found in config, returning 'cluster1'.")
        return "cluster1"

    least_loaded_cluster = min(cluster_loads, key=lambda k: (cluster_loads.get(k, 0), k))

    logger.info(f"Least loaded cluster selected: {least_loaded_cluster}")

    return least_loaded_cluster


async def handle_error(tg_id: int, callback_query: Optional[object] = None, message: str = "") -> None:
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
                await bot.delete_message(chat_id=tg_id, message_id=callback_query.message.message_id)
            except Exception as delete_error:
                logger.warning(f"Не удалось удалить сообщение: {delete_error}")

        await bot.send_message(tg_id, message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка при обработке ошибки: {e}")
