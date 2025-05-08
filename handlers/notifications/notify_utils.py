import asyncio
import os

import aiofiles
import asyncpg

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from database import create_blocked_user
from logger import logger


async def send_messages_with_limit(
    bot: Bot,
    messages: list[dict],
    conn: asyncpg.Connection = None,
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
        tasks = []
        for msg in batch:
            tasks.append(send_notification(bot, msg["tg_id"], msg.get("photo"), msg["text"], msg.get("keyboard")))
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)
        processed_results = []
        for msg, result in zip(batch, batch_results, strict=False):
            tg_id = msg["tg_id"]
            if isinstance(result, bool) and result:
                processed_results.append(True)
            elif isinstance(result, TelegramForbiddenError):
                logger.warning(f"🚫 Бот заблокирован пользователем {tg_id}.")
                if source_file == "special_notifications" and conn:
                    try:
                        await create_blocked_user(tg_id, conn)
                        logger.info(f"Пользователь {tg_id} добавлен в blocked_users.")
                    except Exception:
                        pass
                processed_results.append(False)
            elif isinstance(result, TelegramBadRequest) and "chat not found" in str(result).lower():
                logger.warning(f"🚫 Чат не найден для пользователя {tg_id}.")
                if source_file == "special_notifications" and conn:
                    try:
                        await create_blocked_user(tg_id, conn)
                        logger.info(f"Пользователь {tg_id} добавлен в blocked_users.")
                    except Exception:
                        pass
                processed_results.append(False)
            else:
                logger.warning(f"📩 Не удалось отправить уведомление пользователю {tg_id}.")
                if source_file == "special_notifications" and conn:
                    try:
                        await create_blocked_user(tg_id, conn)
                        logger.info(f"Пользователь {tg_id} добавлен в blocked_users.")
                    except Exception:
                        pass
                processed_results.append(False)
        results.extend(processed_results)
        await asyncio.sleep(1.0)
    return results


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
