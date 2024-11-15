from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage

from config import API_TOKEN, CRYPTO_BOT_ENABLE, FREEKASSA_ENABLE, ROBOKASSA_ENABLE, STARS_ENABLE, YOOKASSA_ENABLE
from middlewares.database import DatabaseMiddleware
from middlewares.logging import LoggingMiddleware

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
router = Router()

from handlers import commands, coupons, donate, notifications, pay, profile, start
from handlers.admin import admin_commands, admin_coupons, admin_panel, admin_user_editor
from handlers.instructions import instructions
from handlers.keys import key_management, keys
from handlers.payments import cryprobot_pay, freekassa_pay, robokassa_pay, stars_pay, yookassa_pay

dp.include_router(admin_commands.router)
dp.include_router(admin_coupons.router)
dp.include_router(admin_panel.router)
dp.include_router(admin_user_editor.router)
dp.include_router(commands.router)
dp.include_router(coupons.router)
dp.include_router(start.router)
dp.include_router(profile.router)
dp.include_router(keys.router)
dp.include_router(key_management.router)
dp.include_router(pay.router)
dp.include_router(notifications.router)
dp.include_router(instructions.router)
dp.include_router(donate.router)
if YOOKASSA_ENABLE:
    dp.include_router(yookassa_pay.router)
if FREEKASSA_ENABLE:
    dp.include_router(freekassa_pay.router)
if CRYPTO_BOT_ENABLE:
    dp.include_router(cryprobot_pay.router)
if STARS_ENABLE:
    dp.include_router(stars_pay.router)
if ROBOKASSA_ENABLE:
    dp.include_router(robokassa_pay.router)

dp.message.middleware(LoggingMiddleware())
dp.callback_query.middleware(LoggingMiddleware())


dp.message.middleware(DatabaseMiddleware())
dp.callback_query.middleware(DatabaseMiddleware())
