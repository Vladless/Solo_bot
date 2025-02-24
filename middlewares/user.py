from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from database import upsert_user
from logger import logger


class UserMiddleware(BaseMiddleware):
    """
    Middleware для обработки информации о пользователе.
    Сохраняет или обновляет данные пользователя в базе данных.
    """
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            # Получаем пользователя из данных события
            if user := data.get("event_from_user"):
                # Получаем сессию из контекста, если она есть
                session = data.get("session")
                await self._process_user(user, session)
        except Exception as e:
            # Логируем ошибку, но не прерываем обработку события
            logger.error(f"Ошибка при обработке пользователя: {e}")
        
        # Продолжаем обработку события в любом случае
        return await handler(event, data)

    async def _process_user(self, user: User, session: Any = None) -> None:
        """
        Обрабатывает информацию о пользователе и сохраняет её в базу данных.
        
        Args:
            user (User): Объект пользователя Telegram
            session (Any, optional): Сессия базы данных, если доступна
        """
        logger.debug(f"Обработка пользователя: {user.id}")
        await upsert_user(
            tg_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
            is_bot=user.is_bot,
            session=session,  # Передаем сессию, если она есть
        )
