import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
import logging

from bot import bot, dp, router
from config import API_TOKEN, WEBHOOK_URL, WEBHOOK_PATH, WEBAPP_HOST, WEBAPP_PORT
from database import init_db, update_balance
from handlers.notifications import notify_expiring_keys
from handlers.pay import payment_webhook  # Импорт обработчика для вебхука ЮKассы

logging.basicConfig(level=logging.DEBUG)

# Функция для периодических уведомлений
async def periodic_notifications():
    while True:
        await notify_expiring_keys(bot)
        await asyncio.sleep(3600)  # Проверяем каждый час

# Действия при старте приложения
async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    await init_db()
    asyncio.create_task(periodic_notifications())

# Действия при завершении приложения
async def on_shutdown(app):
    await bot.delete_webhook()

# Основная функция приложения
async def main():
    # Регистрация роутеров
    dp.include_router(router)

    # Настройка Aiohttp сервера
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Регистрация вебхука для ЮKассы
    app.router.add_post('/yookassa/webhook', payment_webhook)

    # Регистрация вебхука для Telegram бота
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    # Настройка приложения для aiogram
    setup_application(app, dp, bot=bot)

    # Запуск веб-сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEBAPP_HOST, port=WEBAPP_PORT)
    await site.start()

    print(f"Webhook URL: {WEBHOOK_URL}")
    await asyncio.Event().wait()

# Запуск приложения
if __name__ == '__main__':
    asyncio.run(main())

