from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class DeleteMessageMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, (Message, CallbackQuery)):
            if isinstance(event, Message):
                if not event.text.startswith("/start"):
                    try:
                        await event.bot.delete_message(
                            event.chat.id, event.message_id - 1
                        )
                    except Exception:
                        pass
                    await event.delete()
            elif isinstance(event, CallbackQuery):
                await event.answer()
                await event.message.delete()
        return await handler(event, data)
