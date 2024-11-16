import random
import re

import asyncpg

from bot import bot
from config import CLUSTERS, DATABASE_URL
from logger import logger


def sanitize_key_name(key_name: str) -> str:
    return re.sub(r"[^a-z0-9@._-]", "", key_name.lower())


def generate_random_email():
    """Генерирует случайный набор символов."""
    random_string = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=6))
    return random_string


async def get_least_loaded_cluster():
    """
    Функция для получения кластера с наименьшей загрузкой (по количеству ключей).
    Возвращает идентификатор кластера с наименьшей загрузкой или первый кластер из конфигурации,
    если загруженность не определяется. В случае отсутствия кластеров с номером, возвращает 'cluster1'.
    """
    cluster_loads = {}

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        keys = await conn.fetch("SELECT * FROM keys")
        for key in keys:
            cluster_id = key["server_id"]

            if re.match(r"^cluster\d+$", cluster_id):
                if cluster_id not in cluster_loads:
                    cluster_loads[cluster_id] = 0
                cluster_loads[cluster_id] += 1
    finally:
        await conn.close()

    logger.info(f"Cluster loads: {cluster_loads}")

    if not cluster_loads:
        available_clusters = [
            cluster_id
            for cluster_id in CLUSTERS.keys()
            if re.match(r"^cluster\d+$", cluster_id)
        ]

        logger.info(f"Available clusters from config: {available_clusters}")

        if available_clusters:
            logger.info(
                f"Returning the first available cluster: {available_clusters[0]}"
            )
            return available_clusters[0]
        else:
            logger.warning("No valid clusters found in config, returning 'cluster1'.")
            return "cluster1"

    least_loaded_cluster = min(cluster_loads, key=cluster_loads.get)

    logger.info(f"Least loaded cluster selected: {least_loaded_cluster}")

    return least_loaded_cluster


async def handle_error(tg_id, callback_query, message):
    try:
        try:
            await bot.delete_message(
                chat_id=tg_id, message_id=callback_query.message.message_id
            )
        except Exception:
            pass

        await bot.send_message(tg_id, message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка при обработке ошибки: {e}")
