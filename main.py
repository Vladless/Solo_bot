import asyncio
import logging

from aiogram.webhook.aiohttp_server import (SimpleRequestHandler,
                                            setup_application)
from aiohttp import web

from bot import bot, dp, router
from config import WEBAPP_HOST, WEBAPP_PORT, WEBHOOK_PATH, WEBHOOK_URL
from database import init_db
from handlers.notifications import notify_expiring_keys
from handlers.pay import \
    payment_webhook

logging.basicConfig(level=logging.DEBUG)

async def periodic_notifications():
    while True:
        await notify_expiring_keys(bot)
        await asyncio.sleep(3600)

async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    await init_db()
    asyncio.create_task(periodic_notifications())

async def on_shutdown(app):
    await bot.delete_webhook()

async def main():
    dp.include_router(router)

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_post('/yookassa/webhook', payment_webhook)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEBAPP_HOST, port=WEBAPP_PORT)
    await site.start()

    print(f"Webhook URL: {WEBHOOK_URL}")
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())

