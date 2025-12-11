from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, Update
from sqlalchemy.ext.asyncio import AsyncSession

from config import ADMIN_ID
from core.bootstrap import MANAGEMENT_CONFIG
from database.models import Admin


class MaintenanceModeMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        if isinstance(ADMIN_ID, (list, tuple, set)):
            self._admin_ids = set(ADMIN_ID)
        else:
            self._admin_ids = {ADMIN_ID}

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        maintenance_enabled = bool(MANAGEMENT_CONFIG.get("MAINTENANCE_ENABLED", False))
        if not maintenance_enabled:
            return await handler(event, data)

        user_id = None
        if isinstance(event, Message):
            if event.from_user:
                user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            if event.from_user:
                user_id = event.from_user.id

        if not user_id:
            return

        if user_id in self._admin_ids:
            return await handler(event, data)

        session = data.get("session")
        if not isinstance(session, AsyncSession):
            return

        db_admin = await session.get(Admin, user_id)
        if db_admin:
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("⚙️ Бот временно недоступен. Ведутся технические работы.", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("⚙️ Бот временно недоступен. Ведутся технические работы.")

        return
