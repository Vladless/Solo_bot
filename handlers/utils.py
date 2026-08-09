import html
import os
import re
import secrets
import string

from datetime import datetime, timedelta, timezone

import aiofiles

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineQuery,
    InputMediaAnimation,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from database import get_servers
from database.access.resolution import resolve_user_optional
from database.models import Key, Notification, Server
from hooks.processors import process_cluster_balancer
from logger import logger
from settings.config import ADMIN_ID


ALLOWED_GROUP_CODES = ["trial", "discounts", "discounts_max", "cold_discounts", "cold_discounts_max", "gifts"]


_CALLBACK_ANSWER_IGNORE = (
    "query is too old",
    "response timeout expired",
    "query id is invalid",
)

_MESSAGE_NOT_MODIFIED = "message is not modified"


def _is_message_not_modified(exc: BaseException) -> bool:
    return isinstance(exc, TelegramBadRequest) and _MESSAGE_NOT_MODIFIED in str(exc).lower()


async def safe_answer_callback(
    callback_query: CallbackQuery, text: str | None = None, show_alert: bool = False, **kwargs
) -> None:
    """
    Вызывает callback_query.answer(), не поднимая исключение при устаревшем/уже отвеченном callback.
    Использовать в хендлерах после долгой обработки или при наплыве пользователей.
    """
    try:
        await callback_query.answer(text=text, show_alert=show_alert, **kwargs)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if not any(phrase in msg for phrase in _CALLBACK_ANSWER_IGNORE):
            raise


async def safe_answer_inline_query(inline_query: InlineQuery, *args: object, **kwargs: object) -> None:
    """
    Вызывает inline_query.answer(), не поднимая исключение при устаревшем запросе
    (query is too old / response timeout). При нагрузке inline может обрабатываться с задержкой.
    """
    try:
        await inline_query.answer(*args, **kwargs)
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if not any(phrase in msg for phrase in _CALLBACK_ANSWER_IGNORE):
            raise


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
    """Делегирует в services.clusters.select_cluster()."""
    from services.clusters import select_cluster

    result = await select_cluster(session)
    return result.cluster_name


async def check_server_key_limit(server_info: dict, session: AsyncSession) -> bool:
    """Делегирует в services.clusters.check_server_key_limit() с Telegram-callback для уведомлений."""
    from services.clusters import check_server_key_limit as _svc_check

    async def _notify_admin_capacity(server_name: str, total_keys: int, max_keys: int) -> None:
        notif_key = f"server_warn_{server_name}"
        anchor_uid = None
        if ADMIN_ID:
            au = await resolve_user_optional(session, int(ADMIN_ID[0]))
            if au is not None:
                anchor_uid = au.id
        already_sent = None
        if anchor_uid is not None:
            result = await session.execute(
                select(Notification).where(
                    Notification.user_id == anchor_uid,
                    Notification.notification_type == notif_key,
                )
            )
            already_sent = result.scalar_one_or_none()
        if not already_sent:
            for admin_id in ADMIN_ID:
                try:
                    await bot.send_message(
                        admin_id,
                        f"⚠️ Сервер <b>{server_name}</b> почти заполнен ({int(total_keys / max_keys * 100)}%)."
                        f"\nРекомендуется создать новый для балансировки.",
                    )
                except Exception:
                    pass
            if anchor_uid is not None:
                session.add(Notification(user_id=anchor_uid, notification_type=notif_key))

    return await _svc_check(server_info, session, on_capacity_warning=_notify_admin_capacity)


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


class _TemplateValues(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


_CODE_BLOCK_RE = re.compile(r"<code>(.*?)</code>", re.S)
_EMPTY_SECTION_RE = re.compile(r"(?:<b>[^<]*</b>\n)?<blockquote><code></code></blockquote>\n?")


def _row_is_filled(row: str) -> bool:
    """Строка таблицы пуста, если у метки нет значения."""
    _label, sep, value = row.partition(": ")
    return bool(value.strip()) if sep else bool(row.strip())


def _drop_empty_rows(text: str) -> str:
    """Убирает из таблиц строки, у которых не оказалось значения."""

    def replace(match: re.Match) -> str:
        rows = [row for row in match.group(1).split("\n") if _row_is_filled(row)]
        return "<code>" + "\n".join(rows) + "</code>"

    return _CODE_BLOCK_RE.sub(replace, text)


def _drop_empty_sections(text: str) -> str:
    """Убирает блок вместе с его заголовком, если внутри не осталось строк."""
    first_line_end = text.find("\n")
    head, tail = (text[: first_line_end + 1], text[first_line_end + 1 :]) if first_line_end > 0 else (text, "")
    return head + _EMPTY_SECTION_RE.sub("", tail)


_TAG_RE = re.compile(r"<[^>]+>")
_EMPTY_ANCHOR_RE = re.compile(r"<a\s+href=['\"]\s*['\"][^>]*>.*?</a>", re.S | re.I)
_DANGLING_SEPARATOR_RE = re.compile(r"(?m)^\s*[·|]\s*|\s*[·|]\s*$")


def _drop_empty_links(text: str) -> str:
    """Убирает ссылки без адреса и строки-метки, у которых не осталось значения."""
    lines = []
    for line in _EMPTY_ANCHOR_RE.sub("", text).split("\n"):
        cleaned = _DANGLING_SEPARATOR_RE.sub("", line)
        bare = _TAG_RE.sub("", cleaned).strip()
        if bare.endswith(":") and ": " in cleaned + " ":
            _label, sep, value = cleaned.rpartition(":")
            if sep and not _TAG_RE.sub("", value).strip():
                continue
        if not bare and _TAG_RE.sub("", line).strip():
            continue
        lines.append(cleaned)
    return "\n".join(lines)


def fill_text(template: str, **values: object) -> str:
    """Подставляет значения в шаблон и убирает строки и блоки, для которых не оказалось данных."""
    try:
        text = template.format_map(_TemplateValues(values))
    except (IndexError, ValueError) as error:
        logger.warning(f"Некорректный шаблон в файле текстов: {error}")
        return template
    return _drop_empty_sections(_drop_empty_rows(_drop_empty_links(text))).strip("\n")


def render_screen(*parts: str) -> str:
    """Склеивает части экрана и выравнивает значения всех таблиц по одной вертикали."""
    from handlers.admin.panel.headers import align_screen

    return align_screen(join_blocks(*parts))


def render_text(template: str, **values: object) -> str:
    """Собирает экран из одного шаблона."""
    return render_screen(fill_text(template, **values))


def join_blocks(*blocks: str) -> str:
    """Склеивает блоки экрана, пропуская пустые."""
    return "\n".join(block for block in blocks if block)


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
                except Exception as e:
                    if _is_message_not_modified(e):
                        return
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
            except Exception as e:
                if _is_message_not_modified(e):
                    return
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

    caption = getattr(target_message, "caption", None)
    if not force_text and caption is not None:
        try:
            await target_message.edit_caption(caption=text, reply_markup=reply_markup)
            return
        except Exception as e:
            if _is_message_not_modified(e):
                return
    try:
        await target_message.edit_text(
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        )
        return
    except Exception as e:
        if _is_message_not_modified(e):
            return
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
    from services.clusters import is_full_remnawave_cluster as _svc

    return await _svc(cluster_id, session)


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
    current_time = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
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


async def build_support_button(text: str | None = None) -> "InlineKeyboardButton | None":
    from aiogram.types import InlineKeyboardButton

    from settings.buttons import SUPPORT
    from settings.config import SUPPORT_CHAT_URL

    label = text or SUPPORT
    url = (SUPPORT_CHAT_URL or "").strip()
    if url.startswith(("http://", "https://", "tg://")):
        return InlineKeyboardButton(text=label, url=url)

    from core.settings.modes_config import MODES_CONFIG

    if MODES_CONFIG.get("SUPPORT_TICKETS_ENABLED"):
        try:
            from support_bot import support_deeplink

            deeplink = await support_deeplink()
        except Exception:
            deeplink = None
        if deeplink:
            return InlineKeyboardButton(text=label, url=deeplink)
    return None
