from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.storage.memory import MemoryStorage
from config import API_TOKEN
from auth import login_with_credentials

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)
router = Router()

from handlers import start, profile, keys, stats, expiry, balance
from key_management import router as key_management_router


# Регистрация обработчиков
dp.include_router(start.router)
dp.include_router(profile.router)
dp.include_router(keys.router)
dp.include_router(stats.router)
dp.include_router(expiry.router)
dp.include_router(balance.router)
dp.include_router(key_management_router)

async def on_startup(dispatcher: Dispatcher):
    await login_with_credentials()
