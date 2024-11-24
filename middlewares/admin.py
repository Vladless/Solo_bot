from typing import Any, Awaitable, Callable, Dict, Union

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from config import ADMIN_ID


class AdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        data["admin"] = self._check_admin_access(event)
        return await handler(event, data)

    def _check_admin_access(self, event: TelegramObject) -> bool:
        try:
            admin_ids: Union[int, list[int]] = ADMIN_ID
            if isinstance(admin_ids, list):
                return event.chat.id in admin_ids
            return event.chat.id == admin_ids
        except Exception:
            return False
