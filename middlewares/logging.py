from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger
import logging


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id = None
        username = None
        action = None

        if isinstance(event, Message):
            user_id = event.from_user.id
            username = event.from_user.username
            action = f"Сообщение: {event.text}"
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            username = event.from_user.username
            action = f"Обратный вызов: {event.data}"

        logger.info(
            f"Активность пользователя - "
            f"ID пользователя: {user_id}, "
            f"Имя пользователя: {username}, "
            f"Действие: {action}"
        )

        return await handler(event, data)


class InterceptHandler(logging.Handler):
    def emit(self, record):
        level = logger.level(record.levelname).name if logger.level(record.levelname) else record.levelno
        logger.log(level, record.getMessage())

logging.basicConfig(handlers=[InterceptHandler()], level=0)

logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger.add("logs/app.log", level="INFO", rotation="10 MB", compression="zip")