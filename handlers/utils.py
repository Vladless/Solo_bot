import asyncio
import html
import os
import re
import secrets
import string

from collections import OrderedDict
from datetime import datetime, timedelta

import aiofiles

from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from config import ADMIN_ID
from database import get_servers
from database.models import Key, Notification, Server
from hooks.hooks import run_hooks
from logger import logger


ALLOWED_GROUP_CODES = ["trial", "discounts", "discounts_max", "gifts"]


async def generate_random_email(
    length: int = 8,
    session: AsyncSession | None = None,
    max_attempts: int = 20,
) -> str:
    alphabet = string.ascii_lowercase + string.digits
    for _ in range(max_attempts):
        candidate = "".join(secrets.choice(alphabet) for _ in range(length)) if length > 0 else ""
        if not session:
            return candidate
        exists = await session.execute(select(Key.email).where(Key.email == candidate).limit(1))
        if not exists.scalar_one_or_none():
            return candidate
    raise RuntimeError("Не удалось сгенерировать уникальный email после нескольких попыток")


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
        enabled_servers = [server for server in cluster_servers if server.get("enabled", True)]

        if not enabled_servers:
            continue

        available_servers = []
        for server in enabled_servers:
            if await check_server_key_limit(server, session):
                available_servers.append(server)

        if available_servers:
            available_clusters[cluster_name] = cluster_loads[cluster_name]
        else:
            continue

    cluster_filter_results = await run_hooks("cluster_balancer", available_clusters=available_clusters, session=session)
    if cluster_filter_results and cluster_filter_results[0]:
        available_clusters = cluster_filter_results[0]

    if not available_clusters:
        logger.warning("❌ Нет доступных кластеров с лимитом ключей!")
        raise ValueError("⚠️ Сервисы временно недоступны. Попробуйте позже.")

    least_loaded_cluster = min(available_clusters, key=lambda k: (available_clusters[k], k))
    logger.info(
        f"Выбран наименее загруженный кластер: {least_loaded_cluster} (загрузка: {available_clusters[least_loaded_cluster]})"
    )
    return least_loaded_cluster


async def check_server_key_limit(server_info: dict, session: AsyncSession) -> bool:
    server_name = server_info.get("server_name")
    cluster_name = server_info.get("cluster_name")
    max_keys = server_info.get("max_keys")

    if not max_keys:
        return True

    identifier = cluster_name if cluster_name else server_name

    result = await session.execute(select(func.count()).select_from(Key).where(Key.server_id == identifier))
    total_keys = result.scalar() or 0

    if total_keys >= max_keys:
        logger.warning(f"[Key Limit] Сервер {server_name} достиг лимита: {total_keys}/{max_keys}")
        return False

    usage_percent = total_keys / max_keys

    if usage_percent >= 0.9:
        notif_key = f"server_warn_{server_name}"

        result = await session.execute(
            select(Notification).where(Notification.tg_id == 0, Notification.notification_type == notif_key)
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


def get_media_type(media_path: str) -> str:
    if not media_path:
        return "photo"

    ext = os.path.splitext(media_path.lower())[1]

    if ext in [".jpg", ".jpeg", ".png", ".webp"]:
        return "photo"

    if ext in [".mp4", ".mov", ".avi"]:
        return "video"

    if ext == ".gif":
        return "animation"

    return "photo"


async def edit_or_send_message(
    target_message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    media_path: str = None,
    disable_web_page_preview: bool = False,
    force_text: bool = False,
    disable_cache: bool = False,
):
    if not hasattr(edit_or_send_message, "cache"):
        import asyncio

        from collections import OrderedDict

        edit_or_send_message.cache = OrderedDict()
        edit_or_send_message.lock = asyncio.Lock()
        edit_or_send_message.max = 256

    def find_media_file(original_path: str) -> str | None:
        if not original_path:
            return None

        if os.path.isfile(original_path):
            return original_path

        base_name = os.path.splitext(original_path)[0]
        supported_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".avi"]

        for ext in supported_extensions:
            fallback_path = base_name + ext
            if os.path.isfile(fallback_path):
                return fallback_path

        return None

    if media_path:
        actual_media_path = find_media_file(media_path)
        if actual_media_path:
            media_type = get_media_type(actual_media_path)

            cached_id = None
            if not disable_cache:
                async with edit_or_send_message.lock:
                    cached_id = edit_or_send_message.cache.get(actual_media_path)
                    if cached_id:
                        edit_or_send_message.cache.move_to_end(actual_media_path)

            if cached_id:
                try:
                    if media_type == "photo":
                        await target_message.edit_media(
                            InputMediaPhoto(media=cached_id, caption=text), reply_markup=reply_markup
                        )
                    elif media_type == "video":
                        await target_message.edit_media(
                            InputMediaVideo(media=cached_id, caption=text), reply_markup=reply_markup
                        )
                    elif media_type == "animation":
                        await target_message.edit_media(
                            InputMediaAnimation(media=cached_id, caption=text), reply_markup=reply_markup
                        )
                    return
                except Exception:
                    try:
                        if media_type == "photo":
                            await target_message.answer_photo(
                                photo=cached_id,
                                caption=text,
                                reply_markup=reply_markup,
                                disable_web_page_preview=disable_web_page_preview,
                            )
                        elif media_type == "video":
                            await target_message.answer_video(
                                video=cached_id,
                                caption=text,
                                reply_markup=reply_markup,
                                disable_web_page_preview=disable_web_page_preview,
                            )
                        elif media_type == "animation":
                            await target_message.answer_animation(
                                animation=cached_id,
                                caption=text,
                                reply_markup=reply_markup,
                                disable_web_page_preview=disable_web_page_preview,
                            )
                        return
                    except Exception:
                        pass

            async with aiofiles.open(actual_media_path, "rb") as f:
                data = await f.read()
            upload = BufferedInputFile(data, filename=os.path.basename(actual_media_path))

            try:
                if media_type == "photo":
                    msg = await target_message.edit_media(
                        InputMediaPhoto(media=upload, caption=text), reply_markup=reply_markup
                    )
                elif media_type == "video":
                    msg = await target_message.edit_media(
                        InputMediaVideo(media=upload, caption=text), reply_markup=reply_markup
                    )
                elif media_type == "animation":
                    msg = await target_message.edit_media(
                        InputMediaAnimation(media=upload, caption=text), reply_markup=reply_markup
                    )
            except Exception:
                if media_type == "photo":
                    msg = await target_message.answer_photo(
                        photo=upload,
                        caption=text,
                        reply_markup=reply_markup,
                        disable_web_page_preview=disable_web_page_preview,
                    )
                elif media_type == "video":
                    msg = await target_message.answer_video(
                        video=upload,
                        caption=text,
                        reply_markup=reply_markup,
                        disable_web_page_preview=disable_web_page_preview,
                    )
                elif media_type == "animation":
                    msg = await target_message.answer_animation(
                        animation=upload,
                        caption=text,
                        reply_markup=reply_markup,
                        disable_web_page_preview=disable_web_page_preview,
                    )

            file_id = None
            if hasattr(msg, "photo") and msg.photo:
                file_id = msg.photo[-1].file_id
            elif hasattr(msg, "video") and msg.video:
                file_id = msg.video.file_id
            elif hasattr(msg, "animation") and msg.animation:
                file_id = msg.animation.file_id

            if file_id and not disable_cache:
                async with edit_or_send_message.lock:
                    if actual_media_path not in edit_or_send_message.cache:
                        edit_or_send_message.cache[actual_media_path] = file_id
                        if len(edit_or_send_message.cache) > edit_or_send_message.max:
                            edit_or_send_message.cache.popitem(last=False)
            return

    if not force_text and target_message.caption is not None:
        try:
            await target_message.edit_caption(caption=text, reply_markup=reply_markup)
            return
        except Exception:
            pass
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
    result = await session.execute(select(Server.panel_type).where(Server.cluster_name == cluster_id))
    panel_types = result.scalars().all()

    if panel_types:
        return all(pt.lower() == "remnawave" for pt in panel_types)

    result = await session.execute(select(Server.panel_type).where(Server.server_name == cluster_id))
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
    "January": "января",
    "February": "февраля",
    "March": "марта",
    "April": "апреля",
    "May": "мая",
    "June": "июня",
    "July": "июля",
    "August": "августа",
    "September": "сентября",
    "October": "октября",
    "November": "ноября",
    "December": "декабря",
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


def get_username(user) -> str:
    if getattr(user, "full_name", None):
        return html.escape(user.full_name)
    if getattr(user, "first_name", None):
        return html.escape(user.first_name)
    if getattr(user, "username", None):
        return "@" + html.escape(user.username)
    return "Пользователь"


def format_discount_time_left(last_time: datetime, discount_hours: int) -> str:
    expires_at = last_time + timedelta(hours=discount_hours)
    current_time = datetime.utcnow()
    time_left = expires_at - current_time

    if time_left.total_seconds() <= 0:
        return "⏳ Время истекло"

    total_seconds = int(time_left.total_seconds())
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    if days > 0:
        return format_days(days)
    elif hours > 0:
        return format_hours(hours)
    else:
        return format_minutes(minutes)


def extract_user_data(user) -> dict:
    return {
        "tg_id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "language_code": user.language_code,
        "is_bot": user.is_bot,
    }
