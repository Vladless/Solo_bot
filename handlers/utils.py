import json
import os
import re
import secrets
import string

import aiofiles
import aiohttp
import asyncpg

from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

from bot import bot
from config import ADMIN_ID, DATABASE_URL
from database import get_all_keys, get_servers
from logger import logger


def generate_random_email(length: int = 8) -> str:
    """
    Генерирует случайный email с заданной длиной.
    """
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(length)) if length > 0 else ""


async def get_least_loaded_cluster() -> str:
    """
    Возвращает кластер с наименьшей загрузкой, где есть хотя бы один сервер с доступным лимитом.
    """
    servers = await get_servers()
    server_to_cluster = {}
    cluster_loads = {}

    for cluster_name, cluster_servers in servers.items():
        cluster_loads[cluster_name] = 0
        for server in cluster_servers:
            server_to_cluster[server["server_name"]] = cluster_name

    async with asyncpg.create_pool(DATABASE_URL) as pool:
        async with pool.acquire() as conn:
            keys = await get_all_keys(conn)
            for key in keys:
                server_id = key["server_id"]
                cluster_id = server_to_cluster.get(server_id, server_id)
                if cluster_id in cluster_loads:
                    cluster_loads[cluster_id] += 1

            available_clusters = {}
            for cluster_name, cluster_servers in servers.items():
                for server in cluster_servers:
                    if server.get("enabled", True) and await check_server_key_limit(server, conn):
                        available_clusters[cluster_name] = cluster_loads[cluster_name]
                        break

    if not available_clusters:
        logger.warning("❌ Нет доступных кластеров с лимитом ключей!")
        return "cluster1"

    least_loaded_cluster = min(available_clusters, key=lambda k: (available_clusters[k], k))
    logger.info(f"✅ Выбран наименее загруженный кластер с лимитом: {least_loaded_cluster}")

    return least_loaded_cluster


async def check_server_key_limit(server_info: dict, conn) -> bool:
    """
    Универсальная проверка лимита ключей для сервера в режимах кластеров и стран.
    """
    server_name = server_info.get("server_name")
    cluster_name = server_info.get("cluster_name")
    max_keys = server_info.get("max_keys")

    if not max_keys:
        return True

    identifier = cluster_name if cluster_name else server_name
    total_keys = await conn.fetchval("SELECT COUNT(*) FROM keys WHERE server_id = $1", identifier)

    if total_keys >= max_keys:
        logger.warning(f"[Key Limit] Сервер {server_name} достиг лимита: {total_keys}/{max_keys}")
        return False

    usage_percent = total_keys / max_keys

    if usage_percent >= 0.9:
        notif_key = f"server_warn_{server_name}"
        already_sent = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM notifications WHERE tg_id = 0 AND notification_type = $1)", notif_key
        )
        if not already_sent:
            for admin_id in ADMIN_ID:
                try:
                    await bot.send_message(
                        admin_id,
                        f"⚠️ Сервер <b>{server_name}</b> почти заполнен ({int(usage_percent * 100)}%)."
                        f"\nРекомендуется создать новый для балансировки.",
                    )
                except Exception:
                    pass
            await conn.execute(
                "INSERT INTO notifications (tg_id, notification_type) VALUES (0, $1) ON CONFLICT DO NOTHING",
                notif_key,
            )

    return True


async def handle_error(tg_id: int, callback_query: object | None = None, message: str = "") -> None:
    """
    Обрабатывает ошибку, отправляя сообщение пользователю.
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


def get_plural_form(num: int, form1: str, form2: str, form3: str) -> str:
    """Универсальная функция для получения правильной формы множественного числа"""
    n = abs(num) % 100
    if 10 < n < 20:
        return form3
    return {1: form1, 2: form2, 3: form2, 4: form2}.get(n % 10, form3)


def format_months(months: int) -> str:
    """Форматирует количество месяцев с правильным склонением"""
    if months <= 0:
        return "0 месяцев"
    return f"{months} {get_plural_form(months, 'месяц', 'месяца', 'месяцев')}"


def format_days(days: int) -> str:
    """
    Форматирует количество дней с правильным склонением.
    """
    if days <= 0:
        return "0 дней"
    return f"{days} {get_plural_form(days, 'день', 'дня', 'дней')}"


def format_hours(hours: int) -> str:
    """Форматирует количество часов с правильным склонением"""
    if hours <= 0:
        return "0 часов"
    return f"{hours} {get_plural_form(hours, 'час', 'часа', 'часов')}"


def format_minutes(minutes: int) -> str:
    """Форматирует количество минут с правильным склонением"""
    if minutes <= 0:
        return "0 минут"
    return f"{minutes} {get_plural_form(minutes, 'минута', 'минуты', 'минут')}"


async def edit_or_send_message(
    target_message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    media_path: str = None,
    disable_web_page_preview: bool = False,
    force_text: bool = False,
):
    """
    Универсальная функция для редактирования исходного сообщения target_message.
    """
    if media_path and os.path.isfile(media_path):
        async with aiofiles.open(media_path, "rb") as f:
            image_data = await f.read()
        media = InputMediaPhoto(
            media=BufferedInputFile(image_data, filename=os.path.basename(media_path)),
            caption=text,
        )
        try:
            await target_message.edit_media(media=media, reply_markup=reply_markup)
            return
        except Exception:
            await target_message.answer_photo(
                photo=BufferedInputFile(image_data, filename=os.path.basename(media_path)),
                caption=text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            return
    else:
        if not force_text and target_message.caption is not None:
            try:
                await target_message.edit_caption(caption=text, reply_markup=reply_markup)
                return
            except Exception as e:
                logger.error(f"Ошибка редактирования подписи: {e}")
        try:
            await target_message.edit_text(
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            return
        except Exception:
            await target_message.answer(
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )


def convert_to_bytes(value: float, unit: str) -> int:
    """
    Конвертирует значение с указанной единицей измерения в байты.
    """
    KB = 1024
    MB = KB * 1024
    GB = MB * 1024
    TB = GB * 1024
    units = {"KB": KB, "MB": MB, "GB": GB, "TB": TB}
    return int(value * units.get(unit.upper(), 1))


async def is_full_remnawave_cluster(cluster_id: str, session) -> bool:
    """
    Универсальная проверка:
    - Если cluster_id — это имя кластера, проверяет, что все его сервера используют Remnawave.
    - Если cluster_id — это имя одиночного сервера, проверяет, что он Remnawave.
    """
    cluster_servers = await session.fetch(
        "SELECT panel_type FROM servers WHERE cluster_name = $1",
        cluster_id,
    )

    if cluster_servers:
        panel_types = [s["panel_type"].lower() for s in cluster_servers if s.get("panel_type")]
        return all(pt == "remnawave" for pt in panel_types)

    server = await session.fetchrow(
        "SELECT panel_type FROM servers WHERE server_name = $1",
        cluster_id,
    )
    return server and server["panel_type"].lower() == "remnawave"


def sanitize_key_name(key_name: str) -> str:
    """
    Очищает название ключа, оставляя только допустимые символы.

    Args:
        key_name (str): Исходное название ключа.

    Returns:
        str: Очищенное название ключа в нижнем регистре.
    """
    return re.sub(r"[^a-z0-9@._-]", "", key_name.lower())
