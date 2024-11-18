from typing import Any, Awaitable, Callable, Dict

import asyncpg
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import DATABASE_URL


class DatabaseMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with await asyncpg.create_pool(DATABASE_URL) as pool:
            async with pool.acquire() as session:
                data["session"] = session
                try:
                    return await handler(event, data)
                finally:
                    await pool.release(session)
