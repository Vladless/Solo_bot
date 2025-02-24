from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Dict, Optional, Union

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import TelegramObject, Update, User
from cachetools import TTLCache

from logger import logger


class ThrottlingMiddleware(BaseMiddleware):
    """
    Middleware для ограничения частоты запросов от пользователей.
    Позволяет настраивать разные временные интервалы для разных типов запросов.
    """

    def __init__(
        self,
        *,
        default_key: str | None = "default",
        default_ttl: float = 0.5,
        cache_size: int = 10_000,
        **ttl_map: float,
    ) -> None:
        """
        Инициализация middleware для ограничения частоты запросов.

        Args:
            default_key: Ключ по умолчанию для ограничения
            default_ttl: Время ограничения по умолчанию в секундах
            cache_size: Максимальный размер кэша для каждого ключа
            **ttl_map: Словарь с ключами и временем ограничения
        """
        # Добавляем ключ по умолчанию в карту TTL, если он указан
        if default_key:
            ttl_map[default_key] = default_ttl

        self.default_key = default_key
        self.caches: dict[str, MutableMapping[int, None]] = {}
        self.cache_size = cache_size

        # Инициализация кэшей для каждого ключа
        for name, ttl in ttl_map.items():
            self.caches[name] = TTLCache(maxsize=self.cache_size, ttl=ttl)

        logger.debug(f"ThrottlingMiddleware initialized with {len(self.caches)} throttling keys")

    def _should_skip_throttling(self, event: Update) -> bool:
        """
        Проверяет, нужно ли пропустить ограничение для данного события.

        Args:
            event: Событие Telegram

        Returns:
            True, если ограничение следует пропустить
        """
        # Пропускаем предварительные запросы на оплату
        if event.pre_checkout_query:
            return True

        # Пропускаем уведомления об успешной оплате
        if event.message and event.message.successful_payment:
            return True

        return False

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Проверяем, что событие является экземпляром Update
        if not isinstance(event, Update):
            logger.debug(f"Skipping throttling for non-Update event: {type(event).__name__}")
            return await handler(event, data)

        # Проверяем, нужно ли пропустить ограничение
        if self._should_skip_throttling(event):
            logger.debug("Skipping throttling for special event type")
            return await handler(event, data)

        # Получаем пользователя из данных события
        user: User | None = data.get("event_from_user")

        if user is None:
            logger.debug("No user found in event data, proceeding without throttle")
            return await handler(event, data)

        # Получаем ключ ограничения из флагов или используем ключ по умолчанию
        key = get_flag(data, "throttling_key", default=self.default_key)

        if not key:
            logger.debug(f"No throttling key provided for user {user.id}, proceeding without throttle")
            return await handler(event, data)

        # Проверяем, находится ли пользователь в кэше (т.е. ограничен)
        if user.id in self.caches[key]:
            logger.warning(f"User {user.id} is throttled with key: {key}")
            return None

        # Добавляем пользователя в кэш
        self.caches[key][user.id] = None
        logger.debug(f"User {user.id} allowed to proceed with key: {key}")

        # Продолжаем обработку события
        return await handler(event, data)
