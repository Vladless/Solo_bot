from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class DeleteMessageMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, (Message, CallbackQuery)):
            if isinstance(event, Message):
                if not event.entities[0].type == "bot_command" and event.text == "/start":
                    try:
                        await event.bot.delete_message(event.chat.id, event.message_id - 1)
                    except Exception:
                        pass
                    await event.delete()
            elif isinstance(event, CallbackQuery):
                await event.answer()
                await event.message.delete()
        return await handler(event, data)
