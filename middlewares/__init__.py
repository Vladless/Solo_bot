from collections.abc import Iterable

from aiogram import Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from middlewares.ban_checker import BanCheckerMiddleware
from middlewares.subscription import SubscriptionMiddleware

from .admin import AdminMiddleware
from .direct_start_blocker import DirectStartBlockerMiddleware
from .loggings import LoggingMiddleware
from .maintenance import MaintenanceModeMiddleware
from .session import SessionMiddleware
from .throttling import ThrottlingMiddleware
from .user import UserMiddleware


def register_middleware(
    dispatcher: Dispatcher,
    middlewares: Iterable[BaseMiddleware | type[BaseMiddleware]] | None = None,
    exclude: Iterable[str] | None = None,
    pool=None,
    sessionmaker=None,
) -> None:
    """Регистрирует middleware в диспетчере."""
    direct_start_blocker = DirectStartBlockerMiddleware()
    dispatcher.message.outer_middleware(direct_start_blocker)
    dispatcher.callback_query.outer_middleware(direct_start_blocker)

    if middlewares is None:
        available_middlewares = {
            "session": (SessionMiddleware(sessionmaker) if sessionmaker else SessionMiddleware()),
            "admin": AdminMiddleware(),
            "maintenance": MaintenanceModeMiddleware(),
            "logging": LoggingMiddleware(),
            "throttling": ThrottlingMiddleware(),
            "user": UserMiddleware(),
        }

        exclude_set = set(exclude or [])
        middlewares = [middleware for name, middleware in available_middlewares.items() if name not in exclude_set]

    if sessionmaker:
        dispatcher.update.outer_middleware(SubscriptionMiddleware())
        dispatcher.update.outer_middleware(BanCheckerMiddleware(sessionmaker))

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
