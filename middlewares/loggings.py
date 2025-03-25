from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineQuery, Message, TelegramObject, User

from logger import logger


class UserInfo(TypedDict):
    user_id: int | None
    username: str | None
    action: str | None


class LoggingMiddleware(BaseMiddleware):
    """Middleware для логирования действий пользователя."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_info = self._extract_user_info(event)

        if user_info["user_id"]:
            logger.info(
                f"Активность пользователя - "
                f"ID пользователя: {user_info['user_id']}, "
                f"Имя пользователя: {user_info['username'] or 'Не указано'}, "
                f"Действие: {user_info['action'] or 'Неизвестно'}"
            )

        return await handler(event, data)

    def _extract_user_info(self, event: TelegramObject) -> UserInfo:
        """Извлекает информацию о пользователе из различных типов событий.

        Args:
            event: Событие Telegram

        Returns:
            Словарь с информацией о пользователе
        """
        result: UserInfo = {"user_id": None, "username": None, "action": None}

        if hasattr(event, "from_user") and isinstance(event.from_user, User):
            result["user_id"] = event.from_user.id
            result["username"] = event.from_user.username

            if isinstance(event, Message):
                result["action"] = f"Сообщение: {event.text}"
            elif isinstance(event, CallbackQuery):
                result["action"] = f"Обратный вызов: {event.data}"
            elif isinstance(event, InlineQuery):
                result["action"] = f"Inline запрос: {event.query}"

        return result
