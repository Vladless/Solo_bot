from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage

from config import API_TOKEN, CRYPTO_BOT_ENABLE, FREEKASSA_ENABLE, YOOKASSA_ENABLE,STARS_ENABLE
from middlewares.logging import UserActivityMiddleware

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
router = Router()

from handlers import commands, notifications, pay, profile, start
from handlers.admin import admin, admin_panel, user_editor
from handlers.keys import key_management, keys
from handlers.payments import cryprobot_pay, freekassa_pay, yookassa_pay, stars_pay

dp.include_router(admin.router)
dp.include_router(admin_panel.router)
dp.include_router(user_editor.router)
dp.include_router(commands.router)
dp.include_router(start.router)
dp.include_router(profile.router)
dp.include_router(keys.router)
dp.include_router(key_management.router)
dp.include_router(pay.router)
if YOOKASSA_ENABLE:
    dp.include_router(yookassa_pay.router)
if FREEKASSA_ENABLE:
    dp.include_router(freekassa_pay.router)
if CRYPTO_BOT_ENABLE:
    dp.include_router(cryprobot_pay.router)
if STARS_ENABLE:
    dp.include_router(stars_pay.router)
dp.include_router(notifications.router)

dp.message.middleware(UserActivityMiddleware())
dp.callback_query.middleware(UserActivityMiddleware())
