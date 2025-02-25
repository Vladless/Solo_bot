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
                # Обрабатываем пользователя и получаем его данные из БД
                db_user = await self._process_user(user, session)
                # Добавляем данные пользователя из БД в контекст
                if db_user:
                    data["user"] = db_user
        except Exception as e:
            # Логируем ошибку, но не прерываем обработку события
            logger.error(f"Ошибка при обработке пользователя: {e}")

        # Продолжаем обработку события в любом случае
        return await handler(event, data)

    async def _process_user(self, user: User, session: Any = None) -> dict:
        """
        Обрабатывает информацию о пользователе и сохраняет её в базу данных.

        Args:
            user (User): Объект пользователя Telegram
            session (Any, optional): Сессия базы данных, если доступна

        Returns:
            dict: Словарь с информацией о пользователе из базы данных
        """
        logger.debug(f"Обработка пользователя: {user.id}")
        # Получаем данные пользователя из БД после вставки/обновления
        user_data = await upsert_user(
            tg_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
            is_bot=user.is_bot,
            session=session,  # Передаем сессию, если она есть
        )

        logger.debug(f"Получены данные пользователя из БД: {user.id}")
        return user_data
