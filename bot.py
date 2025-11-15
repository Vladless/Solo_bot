from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import API_TOKEN
from filters.private import IsPrivateFilter
from utils.errors import setup_error_handlers
from utils.modules_loader import load_modules_from_folder, modules_hub


bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

dp.include_router(modules_hub)

load_modules_from_folder()

dp.message.filter(IsPrivateFilter())
dp.callback_query.filter(IsPrivateFilter())

setup_error_handlers(dp)
