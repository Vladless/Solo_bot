from collections.abc import Awaitable, Callable
from typing import Any, Dict, Union

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class MessageHandlerMiddleware(BaseMiddleware):
    """
    Middleware для обработки сообщений и callback-запросов.

    Добавляет в контекст обработчика следующие данные:
    - chat_id: ID чата или пользователя
    - target_message: Объект сообщения для ответа или редактирования
    - is_callback: Флаг, указывающий, является ли запрос callback-запросом
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        """
        Обрабатывает входящее событие и добавляет в контекст необходимые данные.

        Args:
            handler: Обработчик события.
            event: Событие (сообщение или callback-запрос).
            data: Словарь с данными контекста.

        Returns:
            Any: Результат выполнения обработчика.
        """
        # Определяем тип события и извлекаем нужные данные
        if isinstance(event, CallbackQuery):
            data["chat_id"] = event.from_user.id
            data["target_message"] = event.message
            data["is_callback"] = True
        else:
            data["chat_id"] = event.chat.id
            data["target_message"] = event
            data["is_callback"] = False

        # Вызываем следующий обработчик
        return await handler(event, data)
