import random
import re

from bot import bot

from config import SERVERS
from loguru import logger


def sanitize_key_name(key_name: str) -> str:
    return re.sub(r"[^a-z0-9@._-]", "", key_name.lower())


def generate_random_email():
    """Генерирует случайный набор символов."""
    random_string = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=6))
    return random_string


async def get_least_loaded_server(conn):
    """Находит сервер с наименьшей загрузкой."""
    least_loaded_server_id = None
    min_load_percentage = float("inf")

    for server_id, server in SERVERS.items():
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM keys WHERE server_id = $1", server_id
        )
        percent_full = (count / 60) * 100 if count <= 60 else 100
        if percent_full < min_load_percentage:
            min_load_percentage = percent_full
            least_loaded_server_id = server_id

    return least_loaded_server_id


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