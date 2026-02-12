from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import ADMIN_ID
from database.models import Admin


class AdminMiddleware(BaseMiddleware):
    """Проверяет, является ли пользователь администратором."""

    _admin_ids: set[int] = set(ADMIN_ID) if isinstance(ADMIN_ID, list | tuple) else {ADMIN_ID}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        data["admin"] = await self._check_admin_access(event, session)
        return await handler(event, data)

    async def _check_admin_access(
        self,
        event: TelegramObject,
        session: AsyncSession | None,
    ) -> bool:
        try:
            user_id = None

            if isinstance(event, Message):
                if event.from_user:
                    user_id = event.from_user.id
            elif isinstance(event, CallbackQuery):
                if event.from_user:
                    user_id = event.from_user.id
            else:
                from_user = getattr(event, "from_user", None)
                if from_user:
                    user_id = getattr(from_user, "id", None)

            if not user_id:
                return False

            if user_id in self._admin_ids:
                return True

            if not session:
                return False

            result = await session.execute(select(Admin).where(Admin.tg_id == user_id))
            return result.scalar_one_or_none() is not None
        except Exception:
            return False
