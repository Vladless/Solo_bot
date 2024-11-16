import asyncio
import signal

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from backup import backup_database
from bot import bot, dp, router
from config import CRYPTO_BOT_ENABLE, DEV_MODE, FREEKASSA_ENABLE, ROBOKASSA_ENABLE, SUB_PATH, WEBAPP_HOST, WEBAPP_PORT, WEBHOOK_PATH, WEBHOOK_URL, YOOKASSA_ENABLE
from database import init_db
from handlers.keys.subscriptions import handle_subscription
from handlers.notifications import notify_expiring_keys
from handlers.payments.cryprobot_pay import cryptobot_webhook
from handlers.payments.freekassa_pay import freekassa_webhook
from handlers.payments.robokassa_pay import robokassa_webhook
from handlers.payments.yookassa_pay import yookassa_webhook
from logger import logger


async def periodic_notifications():
    while True:
        await notify_expiring_keys(bot)
        await asyncio.sleep(1800)


async def periodic_database_backup():
    while True:
        await backup_database()
        await asyncio.sleep(21600)


async def on_startup(app):
    await bot.set_webhook(WEBHOOK_URL)
    await init_db()
    asyncio.create_task(periodic_notifications())
    asyncio.create_task(periodic_database_backup())


async def on_shutdown(app):
    await bot.delete_webhook()
    for task in asyncio.all_tasks():
        task.cancel()
    try:
        await asyncio.gather(*asyncio.all_tasks(), return_exceptions=True)
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


async def shutdown_site(site):
    logger.info("Остановка сайта...")
    await site.stop()
    logger.info("Сервер остановлен.")


async def main():
    dp.include_router(router)

    if DEV_MODE:
        # Запуск в режиме полинга для разработки
        await bot.delete_webhook()
        await init_db()
        await dp.start_polling(bot)
    else:
        # Стандартный режим вебхука
        app = web.Application()
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)
        if YOOKASSA_ENABLE:
            app.router.add_post("/yookassa/webhook", yookassa_webhook)
        if FREEKASSA_ENABLE:
            app.router.add_post("/freekassa/webhook", freekassa_webhook)
        if CRYPTO_BOT_ENABLE:
            app.router.add_post("/cryptobot/webhook", cryptobot_webhook)
        if ROBOKASSA_ENABLE:
            app.router.add_post("/robokassa/webhook", robokassa_webhook)
        app.router.add_get(f"{SUB_PATH}{{email}}", handle_subscription)

        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=WEBAPP_HOST, port=WEBAPP_PORT)
        await site.start()

        logger.info(f"Webhook URL: {WEBHOOK_URL}")

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(shutdown_site(site))
            )

        try:
            await asyncio.Event().wait()
        finally:
            pending = asyncio.all_tasks()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Ошибка при запуске приложения:\n{e}")
