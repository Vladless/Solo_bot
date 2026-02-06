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
            await event.answer()
            if isinstance(event.message, InaccessibleMessage):
                new_message = await bot.send_message(event.message.chat.id, "⏳")
                object.__setattr__(event, "message", new_message)
        return await handler(event, data)
