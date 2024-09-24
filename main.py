import asyncio
from bot import bot, dp, router
from database import init_db
from handlers.notifications import notify_expiring_keys

async def periodic_notifications():
    while True:
        await notify_expiring_keys(bot)
        await asyncio.sleep(3600)  # Проверяем каждый час

async def main():
    await init_db()
    dp.include_router(router)  # Подключение роутера
    asyncio.create_task(periodic_notifications())  # Запуск фоновой задачи для уведомлений
    await dp.start_polling(bot)  # Запуск поллинга

if __name__ == '__main__':
    asyncio.run(main())
