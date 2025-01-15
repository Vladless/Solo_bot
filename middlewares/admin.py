from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import ADMIN_ID


class AdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["admin"] = self._check_admin_access(event)
        return await handler(event, data)

    def _check_admin_access(self, event: TelegramObject) -> bool:
        try:
            admin_ids: int | list[int] = ADMIN_ID
            if isinstance(admin_ids, list):
                return event.from_user.id in admin_ids
            return event.from_user.id == admin_ids
        except Exception:
            return False
