from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
import asyncpg

from config import DATABASE_URL


class DatabaseMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            data["session"] = conn
            return await handler(event, data)
        finally:
            await conn.close()
