from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import ADMIN_ID


class AdminMiddleware(BaseMiddleware):
    """Middleware для проверки прав администратора.

    Добавляет в data['admin'] = True/False в зависимости от того,
    является ли пользователь администратором.
    """

    _admin_ids: set[int] = set(ADMIN_ID) if isinstance(ADMIN_ID, list | tuple) else {ADMIN_ID}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Обрабатывает событие и добавляет флаг администратора в data.

        Args:
            handler: Обработчик события
            event: Событие Telegram
            data: Словарь с данными события

        Returns:
            Результат выполнения обработчика
        """
        data["admin"] = self._check_admin_access(event)
        return await handler(event, data)

    def _check_admin_access(self, event: TelegramObject) -> bool:
        """Проверяет, имеет ли пользователь права администратора.

        Args:
            event: Событие Telegram

        Returns:
            True, если пользователь администратор, иначе False
        """
        try:
            if isinstance(event, Message):
                return event.from_user and event.from_user.id in self._admin_ids
            elif isinstance(event, CallbackQuery):
                return event.from_user and event.from_user.id in self._admin_ids

            user_id = getattr(getattr(event, "from_user", None), "id", None)
            return user_id in self._admin_ids if user_id else False
        except Exception:
            return False
