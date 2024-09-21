import asyncio
from bot import dp, router, bot
from database import init_db

async def main():
    await init_db()
    dp.include_router(router)  # Подключение роутера
    await dp.start_polling(bot)  # Запуск поллинга

if __name__ == '__main__':
    asyncio.run(main())