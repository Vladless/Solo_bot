from aiogram import BaseMiddleware
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config import DATABASE_URL


class SessionMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker=None) -> None:
        super().__init__()
        if sessionmaker is None:
            engine = create_async_engine(DATABASE_URL, pool_size=20, max_overflow=0)
            self.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        else:
            self.sessionmaker = sessionmaker

    async def __call__(self, handler, event, data):
        async with self.sessionmaker() as session:
            data["session"] = session
            return await handler(event, data)
