from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, Update

from config import ADMIN_ID
from core.bootstrap import MANAGEGENT_CONFIG
from database import async_session_maker
from database.models import Admin


class MaintenanceModeMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        maintenance_enabled = bool(MANAGEGENT_CONFIG.get("MAINTENANCE_ENABLED", False))
        if not maintenance_enabled:
            return await handler(event, data)

        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if not user_id:
            return

        if user_id in ADMIN_ID:
            return await handler(event, data)

        async with async_session_maker() as session:
            db_admin = await session.get(Admin, user_id)
            if db_admin:
                return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("⚙️ Бот временно недоступен. Ведутся технические работы.", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("⚙️ Бот временно недоступен. Ведутся технические работы.")

        return
