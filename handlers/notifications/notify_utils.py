import asyncio
import os

from datetime import datetime

import aiofiles
import pytz

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from database import create_blocked_user, get_tariff_by_id
from handlers.utils import format_hours
from logger import logger


async def send_messages_with_limit(
    bot: Bot,
    messages: list[dict],
    session: AsyncSession = None,
    source_file: str = None,
    messages_per_second: int = 25,
):
    """
    Отправляет сообщения с ограничением по количеству сообщений в секунду.
    Возвращает список результатов отправки (True для успеха, False для ошибки).
    """
    batch_size = messages_per_second
    results = []

    for i in range(0, len(messages), batch_size):
        batch = messages[i : i + batch_size]
        tasks = [
            send_notification(bot, msg["tg_id"], msg.get("photo"), msg["text"], msg.get("keyboard")) for msg in batch
        ]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for msg, result in zip(batch, batch_results, strict=False):
            tg_id = msg["tg_id"]

            if isinstance(result, bool) and result:
                results.append(True)
            elif isinstance(result, TelegramForbiddenError):
                logger.warning(f"🚫 Бот заблокирован пользователем {tg_id}.")
                await try_add_blocked_user(tg_id, session, source_file)
                results.append(False)
            elif isinstance(result, TelegramBadRequest) and "chat not found" in str(result).lower():
                logger.warning(f"🚫 Чат не найден для пользователя {tg_id}.")
                await try_add_blocked_user(tg_id, session, source_file)
                results.append(False)
            else:
                logger.warning(f"📩 Не удалось отправить уведомление пользователю {tg_id}.")
                await try_add_blocked_user(tg_id, session, source_file)
                results.append(False)

        await asyncio.sleep(1.0)

    return results


async def try_add_blocked_user(tg_id: int, session: AsyncSession, source_file: str | None):
    """
    Добавляет пользователя в список заблокировавших бота, если требуется.
    """
    if source_file == "special_notifications" and session:
        try:
            await create_blocked_user(session, tg_id)
            logger.info(f"Пользователь {tg_id} добавлен в blocked_users.")
        except Exception as e:
            logger.warning(f"Не удалось добавить {tg_id} в blocked_users: {e}")


def rate_limited_send(func):
    async def wrapper(*args, **kwargs):
        while True:
            try:
                return await func(*args, **kwargs)
            except TelegramRetryAfter as e:
                retry_in = int(e.retry_after) + 1
                logger.warning(f"⚠️ Flood control: повтор через {retry_in} сек.")
                await asyncio.sleep(retry_in)
            except TelegramForbiddenError:
                tg_id = kwargs.get("tg_id") or args[1]
                logger.warning(f"🚫 Бот заблокирован пользователем {tg_id}.")
                return False
            except TelegramBadRequest:
                tg_id = kwargs.get("tg_id") or args[1]
                logger.warning(f"🚫 Чат не найден для пользователя {tg_id}.")
                return False
            except Exception as e:
                tg_id = kwargs.get("tg_id") or args[1]
                logger.error(f"❌ Ошибка отправки сообщения пользователю {tg_id}: {e}")
                return False

    return wrapper


async def send_notification(
    bot: Bot,
    tg_id: int,
    image_filename: str | None,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    """
    Отправляет уведомление пользователю.
    """
    if image_filename is None:
        return await _send_text_notification(bot, tg_id, caption, keyboard)

    photo_path = os.path.join("img", image_filename)
    if os.path.isfile(photo_path):
        return await _send_photo_notification(bot, tg_id, photo_path, image_filename, caption, keyboard)
    else:
        logger.warning(f"Файл с изображением не найден: {photo_path}")
        return await _send_text_notification(bot, tg_id, caption, keyboard)


@rate_limited_send
async def _send_photo_notification(
    bot: Bot,
    tg_id: int,
    photo_path: str,
    image_filename: str,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    """Отправляет уведомление с изображением."""
    try:
        async with aiofiles.open(photo_path, "rb") as image_file:
            image_data = await image_file.read()
        buffered_photo = BufferedInputFile(image_data, filename=image_filename)
        await bot.send_photo(tg_id, buffered_photo, caption=caption, reply_markup=keyboard)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except Exception as e:
        logger.error(f"Ошибка отправки фото для пользователя {tg_id}: {e}")
        return await _send_text_notification(bot, tg_id, caption, keyboard)


@rate_limited_send
async def _send_text_notification(
    bot: Bot,
    tg_id: int,
    caption: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> bool:
    """Отправляет текстовое уведомление."""
    try:
        await bot.send_message(tg_id, caption, reply_markup=keyboard)
        return True
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except Exception as e:
        logger.error(f"Неизвестная ошибка при отправке сообщения для пользователя {tg_id}: {e}")
        return False


async def prepare_key_expiry_data(key, session: AsyncSession, current_time: int) -> dict:
    moscow_tz = pytz.timezone("Europe/Moscow")

    expiry_timestamp = key.expiry_time
    hours_left = int((expiry_timestamp - current_time) / (1000 * 3600))
    hours_left_formatted = (
        f"⏳ Осталось времени: {format_hours(hours_left)}" if hours_left > 0 else "⏳ Последний день подписки!"
    )

    expiry_datetime = datetime.fromtimestamp(expiry_timestamp / 1000, tz=moscow_tz)
    formatted_expiry_date = expiry_datetime.strftime("%d %B %Y, %H:%M (МСК)")

    tariff_name = "—"
    tariff_details = ""

    if getattr(key, "tariff_id", None):
        tariff = await get_tariff_by_id(session, key.tariff_id)
        if tariff:
            tariff_name = tariff.get("name") or "—"
            traffic_limit = tariff.get("traffic_limit") or 0
            device_limit = tariff.get("device_limit") or 0
            subgroup_title = tariff.get("subgroup_title", "")
            traffic_text = "безлимит" if traffic_limit <= 0 else f"{traffic_limit} ГБ"
            devices_text = "безлимит" if device_limit <= 0 else str(device_limit)
            lines = []
            if subgroup_title:
                lines.append(subgroup_title)
            lines.append(f"Трафик: {traffic_text}")
            lines.append(f"Устройств: {devices_text}")
            tariff_details = "\n" + "\n".join(lines)

    return {
        "hours_left_formatted": hours_left_formatted,
        "formatted_expiry_date": formatted_expiry_date,
        "tariff_name": tariff_name,
        "tariff_details": tariff_details,
    }
