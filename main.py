from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot import bot, dp, router
from config import API_TOKEN, WEBHOOK_URL, WEBHOOK_PATH, WEBAPP_HOST, WEBAPP_PORT
from database import init_db
from handlers.notifications import notify_expiring_keys

import asyncio

async def periodic_notifications():
    while True:
        await notify_expiring_keys(bot)
        await asyncio.sleep(3600)  # Проверяем каждый час

async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    await init_db()
    asyncio.create_task(periodic_notifications())

async def on_shutdown(app):
    await bot.delete_webhook()

async def main():
    # Регистрация роутеров
    dp.include_router(router)

    # Настройка Aiohttp сервера
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)
    
    # Запуск веб-сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEBAPP_HOST, port=WEBAPP_PORT)
    await site.start()

    print(f"Webhook URL: {WEBHOOK_URL}")
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
