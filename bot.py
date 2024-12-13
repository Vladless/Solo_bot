import traceback

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from config import API_TOKEN

from logger import logger
from middlewares.admin import AdminMiddleware
from middlewares.database import DatabaseMiddleware
from middlewares.delete import DeleteMessageMiddleware
from middlewares.logging import LoggingMiddleware
from middlewares.user import UserMiddleware

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

dp.message.middleware(LoggingMiddleware())
dp.callback_query.middleware(LoggingMiddleware())

dp.message.middleware(AdminMiddleware())
dp.callback_query.middleware(AdminMiddleware())

dp.message.middleware(UserMiddleware())
dp.callback_query.middleware(UserMiddleware())

dp.message.middleware(DatabaseMiddleware())
dp.callback_query.middleware(DatabaseMiddleware())

dp.message.outer_middleware(DeleteMessageMiddleware())
dp.callback_query.outer_middleware(DeleteMessageMiddleware())


@dp.error()
async def error_handler(event: ErrorEvent):
    logger.error(
        "Ошибка в боте:\n"
        f"Исключение: {event.exception}\n"
        f"Тип: {type(event.exception)}\n"
        f"Update: {event.update}\n"
        f"Трассировка:\n{traceback.format_exc()}"
    )
