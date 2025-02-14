from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from config import DATABASE_URL


class SessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            data["session"] = conn
            return await handler(event, data)
        finally:
            await conn.close()
