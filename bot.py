from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import API_TOKEN
from filters.private import IsPrivateFilter
from utils.button_icons import apply_button_icons_patch, set_button_icon_config
from utils.custom_emojis import initialize_custom_emojis
from utils.errors import setup_error_handlers
from utils.modules_loader import load_modules_from_folder, modules_hub

apply_button_icons_patch()

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

dp.include_router(modules_hub)

load_modules_from_folder()

from handlers.buttons import BUTTON_ICON_CONFIG

set_button_icon_config(BUTTON_ICON_CONFIG)

dp.message.filter(IsPrivateFilter())
dp.callback_query.filter(IsPrivateFilter())

setup_error_handlers(dp)
initialize_custom_emojis()
