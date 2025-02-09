from typing import Any

from aiogram import Dispatcher

from .admin import AdminMiddleware
from .delete import DeleteMessageMiddleware
from .loggings import LoggingMiddleware
from .session import SessionMiddleware
from .throttling import ThrottlingMiddleware
from .user import UserMiddleware


def register_middleware(dispatcher: Dispatcher) -> None:
    middlewares = [
        AdminMiddleware(),
        SessionMiddleware(),
        DeleteMessageMiddleware(),
        LoggingMiddleware(),
        ThrottlingMiddleware(),
        UserMiddleware(),
    ]

    for middleware in middlewares:
        dispatcher.message.outer_middleware(middleware)
        dispatcher.callback_query.outer_middleware(middleware)
        dispatcher.inline_query.outer_middleware(middleware)
