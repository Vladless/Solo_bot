from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineQuery, Message, TelegramObject

from logger import logger


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_info = self._extract_user_info(event)

        logger.info(
            f"Активность пользователя - "
            f"ID пользователя: {user_info['user_id']}, "
            f"Имя пользователя: {user_info['username']}, "
            f"Действие: {user_info['action']}"
        )
        return await handler(event, data)

    def _extract_user_info(self, event: TelegramObject) -> dict[str, str | None]:
        user_id = None
        username = None
        action = None

        if isinstance(event, Message):
            user = event.from_user
            user_id = user.id
            username = user.username
            action = f"Сообщение: {event.text}"
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            user_id = user.id
            username = user.username
            action = f"Обратный вызов: {event.data}"
        elif isinstance(event, InlineQuery):
            user = event.from_user
            user_id = user.id
            username = user.username
            action = f"Inline запрос: {event.query}"

        return {"user_id": user_id, "username": username, "action": action}
