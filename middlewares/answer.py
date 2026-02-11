from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InaccessibleMessage, TelegramObject

from bot import bot


class CallbackAnswerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            try:
                await event.answer()
            except Exception:
                pass
            if isinstance(event.message, InaccessibleMessage):
                try:
                    new_message = await bot.send_message(event.message.chat.id, "⏳")
                    object.__setattr__(event, "message", new_message)
                except Exception:
                    pass
        return await handler(event, data)
