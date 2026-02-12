from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, Update

from core.bootstrap import MANAGEMENT_CONFIG


class MaintenanceModeMiddleware(BaseMiddleware):
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

        if data.get("admin"):
            return await handler(event, data)

        if isinstance(event, CallbackQuery):
            await event.answer("⚙️ Бот временно недоступен. Ведутся технические работы.", show_alert=True)
        elif isinstance(event, Message):
            await event.answer("⚙️ Бот временно недоступен. Ведутся технические работы.")

        return
