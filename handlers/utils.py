import os
import re
import secrets
import string
from datetime import datetime

import aiofiles
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from config import ADMIN_ID
from database import get_servers
from database.models import Key, Server
from logger import logger


def generate_random_email(length: int = 8) -> str:
    """
    Генерирует случайный email с заданной длиной.
    """
    return (
        "".join(
            secrets.choice(string.ascii_lowercase + string.digits)
            for _ in range(length)
        )
        if length > 0
        else ""
    )


async def get_least_loaded_cluster(session: AsyncSession) -> str:
    servers = await get_servers(session)
    server_to_cluster = {}
    cluster_loads = {}

    for cluster_name, cluster_servers in servers.items():
        cluster_loads[cluster_name] = 0
        for server in cluster_servers:
            server_to_cluster[server["server_name"]] = cluster_name

    result = await session.execute(select(Key))
    keys = result.scalars().all()

    for key in keys:
        server_id = key.server_id
        cluster_id = server_to_cluster.get(server_id, server_id)
        if cluster_id in cluster_loads:
            cluster_loads[cluster_id] += 1

    available_clusters = {}
    for cluster_name, cluster_servers in servers.items():
        for server in cluster_servers:
            if server.get("enabled", True) and await check_server_key_limit(
                server, session
            ):
                available_clusters[cluster_name] = cluster_loads[cluster_name]
                break

    if not available_clusters:
        logger.warning("❌ Нет доступных кластеров с лимитом ключей!")
        return "cluster1"

    least_loaded_cluster = min(
        available_clusters, key=lambda k: (available_clusters[k], k)
    )
    logger.info(
        f"✅ Выбран наименее загруженный кластер с лимитом: {least_loaded_cluster}"
    )
    return least_loaded_cluster


async def check_server_key_limit(server_info: dict, session: AsyncSession) -> bool:
    from database.models import Key, Notification

    server_name = server_info.get("server_name")
    cluster_name = server_info.get("cluster_name")
    max_keys = server_info.get("max_keys")

    if not max_keys:
        return True

    identifier = cluster_name if cluster_name else server_name

    result = await session.execute(
        select(func.count()).select_from(Key).where(Key.server_id == identifier)
    )
    total_keys = result.scalar() or 0

    if total_keys >= max_keys:
        logger.warning(
            f"[Key Limit] Сервер {server_name} достиг лимита: {total_keys}/{max_keys}"
        )
        return False

    usage_percent = total_keys / max_keys

    if usage_percent >= 0.9:
        notif_key = f"server_warn_{server_name}"

        result = await session.execute(
            select(Notification).where(
                Notification.tg_id == 0, Notification.notification_type == notif_key
            )
        )
        already_sent = result.scalar_one_or_none()

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

            session.add(Notification(tg_id=0, notification_type=notif_key))
            await session.commit()

    return True


async def handle_error(
    tg_id: int, callback_query: object | None = None, message: str = ""
) -> None:
    """
    Обрабатывает ошибку, отправляя сообщение пользователю.
    """
    try:
        if callback_query and hasattr(callback_query, "message"):
            try:
                await bot.delete_message(
                    chat_id=tg_id, message_id=callback_query.message.message_id
                )
            except Exception as delete_error:
                logger.warning(f"Не удалось удалить сообщение: {delete_error}")

        await bot.send_message(tg_id, message, parse_mode=None)

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
    reply_markup: InlineKeyboardMarkup | None = None,
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
                photo=BufferedInputFile(
                    image_data, filename=os.path.basename(media_path)
                ),
                caption=text,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            return
    else:
        if not force_text and target_message.caption is not None:
            try:
                await target_message.edit_caption(
                    caption=text, reply_markup=reply_markup
                )
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


async def is_full_remnawave_cluster(cluster_id: str, session: AsyncSession) -> bool:
    result = await session.execute(
        select(Server.panel_type).where(Server.cluster_name == cluster_id)
    )
    panel_types = result.scalars().all()

    if panel_types:
        return all(pt.lower() == "remnawave" for pt in panel_types)

    result = await session.execute(
        select(Server.panel_type).where(Server.server_name == cluster_id)
    )
    panel_type = result.scalar_one_or_none()
    return panel_type and panel_type.lower() == "remnawave"


def sanitize_key_name(key_name: str) -> str:
    """
    Очищает название ключа, оставляя только допустимые символы.

    Args:
        key_name (str): Исходное название ключа.

    Returns:
        str: Очищенное название ключа в нижнем регистре.
    """
    return re.sub(r"[^a-z0-9@._-]", "", key_name.lower())


RUSSIAN_MONTHS = {
    "January": "Января",
    "February": "Февраля",
    "March": "Марта",
    "April": "Апреля",
    "May": "Мая",
    "June": "Июня",
    "July": "Июля",
    "August": "Августа",
    "September": "Сентября",
    "October": "Октября",
    "November": "Ноября",
    "December": "Декабря",
}


def get_russian_month(date: datetime) -> str:
    """
    Преобразует английское название месяца в русское.

    Args:
        date: Объект datetime, из которого извлекается месяц.

    Returns:
        Название месяца на русском языке.
    """
    english_month = date.strftime("%B")
    return RUSSIAN_MONTHS.get(english_month, english_month)
