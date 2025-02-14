import os

import aiofiles
from aiogram import Bot, types
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup

from logger import logger


async def send_notification(
    bot: Bot,
    tg_id: int,
    image_filename: str,
    caption: str,
    keyboard: InlineKeyboardMarkup,
):
    """
    Отправляет уведомление с изображением, если файл существует, иначе отправляет текстовое сообщение.
    Если возникает TelegramForbiddenError (например, бот заблокирован пользователем),
    функция логирует ошибку и прекращает попытки отправки уведомления.
    """
    photo_path = os.path.join("img", image_filename)
    if os.path.isfile(photo_path):
        try:
            async with aiofiles.open(photo_path, "rb") as image_file:
                image_data = await image_file.read()
            buffered_photo = BufferedInputFile(image_data, filename=image_filename)
            await bot.send_photo(tg_id, buffered_photo, caption=caption, reply_markup=keyboard)
        except TelegramForbiddenError as e:
            logger.error(f"Ошибка отправки фото для пользователя {tg_id}: {e}")
            return
        except Exception as e:
            logger.error(f"Ошибка отправки фото для пользователя {tg_id}: {e}")
            try:
                await bot.send_message(tg_id, caption, reply_markup=keyboard)
            except TelegramForbiddenError as e:
                logger.error(f"Ошибка отправки fallback-сообщения для пользователя {tg_id}: {e}")
                return
            except Exception as e:
                logger.error(f"Неизвестная ошибка при отправке fallback-сообщения для пользователя {tg_id}: {e}")
                return
    else:
        logger.error(f"Файл с изображением не найден: {photo_path}")
        try:
            await bot.send_message(tg_id, caption, reply_markup=keyboard)
        except TelegramForbiddenError as e:
            logger.error(f"Ошибка отправки сообщения для пользователя {tg_id}: {e}")
            return
        except Exception as e:
            logger.error(f"Неизвестная ошибка при отправке сообщения для пользователя {tg_id}: {e}")
            return
