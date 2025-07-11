from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from config import ADMIN_ID
from database.models import Admin


class AdminMiddleware(BaseMiddleware):
    """Middleware для проверки прав администратора.

    Добавляет в data['admin'] = True/False в зависимости от того,
    является ли пользователь администратором.
    """

    _admin_ids: set[int] = (
        set(ADMIN_ID) if isinstance(ADMIN_ID, list | tuple) else {ADMIN_ID}
    )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        """Обрабатывает событие и добавляет флаг администратора в data."""
        data["admin"] = await self._check_admin_access(event, data.get("session"))
        return await handler(event, data)

    async def _check_admin_access(self, event: TelegramObject, session) -> bool:
        """Проверяет, имеет ли пользователь права администратора."""
        try:
            user_id = None
            if isinstance(event, Message):
                user_id = event.from_user.id if event.from_user else None
            elif isinstance(event, CallbackQuery):
                user_id = event.from_user.id if event.from_user else None
            else:
                user_id = getattr(getattr(event, "from_user", None), "id", None)

            if not user_id:
                return False

            if user_id in self._admin_ids:
                return True

            if session:
                result = await session.execute(select(Admin).where(Admin.tg_id == user_id))
                return result.scalar_one_or_none() is not None

            return False
        except Exception:
            return False
