from collections.abc import Iterable

from aiogram import Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from .admin import AdminMiddleware
from .loggings import LoggingMiddleware
from .session import SessionMiddleware
from .throttling import ThrottlingMiddleware
from .user import UserMiddleware
from .maintenance import MaintenanceModeMiddleware



def register_middleware(
    dispatcher: Dispatcher,
    middlewares: Iterable[BaseMiddleware | type[BaseMiddleware]] | None = None,
    exclude: Iterable[str] | None = None,
) -> None:
    """Регистрирует middleware в диспетчере.
    """
    if middlewares is None:
        available_middlewares = {
            "admin": AdminMiddleware(),
            "session": SessionMiddleware(),
            "maintenance": MaintenanceModeMiddleware(), 
            "logging": LoggingMiddleware(),
            "throttling": ThrottlingMiddleware(),
            "user": UserMiddleware(),
        }

        exclude_set = set(exclude or [])
        middlewares = [middleware for name, middleware in available_middlewares.items() if name not in exclude_set]

    handlers = [
        dispatcher.message,
        dispatcher.callback_query,
        dispatcher.inline_query,
    ]

    for middleware in middlewares:
        if isinstance(middleware, type):
            middleware = middleware()

        for handler in handlers:
            handler.outer_middleware(middleware)
