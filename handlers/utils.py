import json
import re
import secrets
import string

import aiohttp
import asyncpg

from bot import bot
from config import DATABASE_URL
from database import get_all_keys, get_servers
from logger import logger


async def get_usd_rate():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.cbr-xml-daily.ru/daily_json.js") as response:
                if response.status == 200:
                    data = await response.text()
                    usd = float(json.loads(data)["Valute"]["USD"]["Value"])
                else:
                    usd = float(100)
    except Exception as e:
        logger.exception(f"Error fetching USD rate: {e}")
        usd = float(100)
    return usd


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
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length)) if length > 0 else ""


async def get_least_loaded_cluster() -> str:
    """
    Определяет кластер с наименьшей загрузкой.

    Returns:
        str: Идентификатор наименее загруженного кластера.
    """
    servers = await get_servers()

    cluster_loads: dict[str, int] = {cluster_id: 0 for cluster_id in servers.keys()}

    async with asyncpg.create_pool(DATABASE_URL) as pool:
        async with pool.acquire() as conn:
            keys = await get_all_keys(conn)
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


async def handle_error(tg_id: int, callback_query: object | None = None, message: str = "") -> None:
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

        await bot.send_message(tg_id, message)

    except Exception as e:
        logger.error(f"Ошибка при обработке ошибки: {e}")


def format_time_until_deletion(seconds: int) -> str:
    if seconds <= 0:
        return "0 минут"

    days = seconds // (3600 * 24)
    hours = (seconds % (3600 * 24)) // 3600
    minutes = (seconds % 3600 + 59) // 60

    parts = []

    if days > 0:
        if days == 1:
            parts.append(f"{days} день")
        elif 2 <= days <= 4:
            parts.append(f"{days} дня")
        else:
            parts.append(f"{days} дней")

    if hours > 0:
        if hours == 1:
            parts.append(f"{hours} час")
        elif 2 <= hours <= 4:
            parts.append(f"{hours} часа")
        else:
            parts.append(f"{hours} часов")

    if minutes > 0 and days == 0:
        if minutes == 1:
            parts.append("1 минута")
        elif 2 <= minutes <= 4:
            parts.append(f"{minutes} минуты")
        else:
            parts.append(f"{minutes} минут")

    return " и ".join(parts) if parts else "менее минуты"
