import asyncio

from bot import bot, dp, router
from database import init_db
from key_management import notify_expiring_keys


async def main():
    await init_db()
    asyncio.create_task(notify_expiring_keys())
    dp.include_router(router)  # Подключение роутера
    await dp.start_polling(bot)  # Запуск поллинга

if __name__ == '__main__':
    asyncio.run(main())