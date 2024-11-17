from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import ADMIN_ID
from logger import logger


class AdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        try:
            if isinstance(ADMIN_ID, list):
                if event.from_user.id in ADMIN_ID:
                    data["admin"] = True
            elif isinstance(ADMIN_ID, int):
                if event.from_user.id == ADMIN_ID:
                    data["admin"] = True
        except Exception as e:
            logger.error(e)
            data["admin"] = False
        return await handler(event, data)
