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

        target = event
        if isinstance(event, Update):
            target = event.message or event.callback_query or event.inline_query
            if target is None:
                return await handler(event, data)

        from_user = getattr(target, "from_user", None)
        if not getattr(from_user, "id", None):
            return

        if data.get("admin"):
            return await handler(event, data)

        if isinstance(target, CallbackQuery):
            await target.answer("⚙️ Бот временно недоступен. Ведутся технические работы.", show_alert=True)
        elif isinstance(target, Message):
            await target.answer("⚙️ Бот временно недоступен. Ведутся технические работы.")

        return
