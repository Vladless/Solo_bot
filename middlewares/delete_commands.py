from aiogram import BaseMiddleware
from aiogram.types import Message


class DeleteCommandMiddleware(BaseMiddleware):
    name = "delete_commands"

    async def __call__(self, handler, event, data):
        result = await handler(event, data)
        if isinstance(event, Message) and event.text and event.text.startswith("/"):
            try:
                await event.delete()
            except Exception:
                pass
        return result
