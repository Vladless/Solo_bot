from collections.abc import Iterable

from aiogram import Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from .admin import AdminMiddleware
from .loggings import LoggingMiddleware
from .session import SessionMiddleware
from .throttling import ThrottlingMiddleware
from .user import UserMiddleware


def register_middleware(
    dispatcher: Dispatcher,
    middlewares: Iterable[BaseMiddleware | type[BaseMiddleware]] | None = None,
    exclude: Iterable[str] | None = None,
) -> None:
    """Регистрирует middleware в диспетчере.

    Args:
        dispatcher: Экземпляр диспетчера Aiogram
        middlewares: Опциональный список middleware для регистрации.
                    Если не указан, регистрируются все стандартные middleware.
        exclude: Опциональный список имен middleware, которые нужно исключить из регистрации.
                Применяется только если middlewares не указан.
    """
    # Если middleware не указаны, используем стандартный набор
    if middlewares is None:
        # Словарь всех доступных middleware
        available_middlewares = {
            "admin": AdminMiddleware(),
            "session": SessionMiddleware(),
            "logging": LoggingMiddleware(),
            "throttling": ThrottlingMiddleware(),
            "user": UserMiddleware(),
        }

        # Фильтруем middleware по списку исключений
        exclude_set = set(exclude or [])
        middlewares = [middleware for name, middleware in available_middlewares.items() if name not in exclude_set]

    # Регистрируем middleware для всех типов обработчиков
    handlers = [
        dispatcher.message,
        dispatcher.callback_query,
        dispatcher.inline_query,
        # Можно добавить другие типы обработчиков при необходимости
    ]

    # Регистрируем каждый middleware для каждого типа обработчика
    for middleware in middlewares:
        # Если передан класс, а не экземпляр, создаем экземпляр
        if isinstance(middleware, type):
            middleware = middleware()

        for handler in handlers:
            handler.outer_middleware(middleware)
