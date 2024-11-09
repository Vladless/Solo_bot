from functools import wraps
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import ADMIN_ID


class AdminMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Проверяем, является ли пользователь администратором
        user_id = None

        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if user_id not in int(ADMIN_ID):
            data["is_admin"] = False
        else:
            data["is_admin"] = True

        return await handler(event, data)


def admin_only():
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Извлекаем объект события (Message или CallbackQuery)
            event = args[0]

            # Определяем ID пользователя
            user_id = event.from_user.id if hasattr(event, "from_user") else None

            if user_id not in int(ADMIN_ID):
                # Можно отправить сообщение или просто return
                if isinstance(event, Message):
                    await event.answer("У вас нет доступа к этой команде.")
                elif isinstance(event, CallbackQuery):
                    await event.answer(
                        "У вас нет доступа к этому действию.", show_alert=True
                    )
                return None

            return await func(*args, **kwargs)

        return wrapper

    return decorator
