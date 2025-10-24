from collections.abc import Iterable

from aiogram import Dispatcher
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from config import CHANNEL_REQUIRED, DISABLE_DIRECT_START
from middlewares.ban_checker import BanCheckerMiddleware
from middlewares.subscription import SubscriptionMiddleware

from .admin import AdminMiddleware
from .answer import CallbackAnswerMiddleware
from .direct_start_blocker import DirectStartBlockerMiddleware
from .loggings import LoggingMiddleware
from .maintenance import MaintenanceModeMiddleware
from .probe import MiddlewareProbe, StreamProbeMiddleware, TailHandlerProbe
from .session import SessionMiddleware
from .throttling import ThrottlingMiddleware
from .user import UserMiddleware


PROBE_LOGGING = False


def register_middleware(
    dispatcher: Dispatcher,
    middlewares: Iterable[BaseMiddleware | type[BaseMiddleware]] | None = None,
    exclude: Iterable[str] | None = None,
    pool=None,
    sessionmaker=None,
) -> None:
    def wrap(mw, name: str):
        return MiddlewareProbe(mw, name) if PROBE_LOGGING else mw

    if PROBE_LOGGING:
        dispatcher.update.outer_middleware(StreamProbeMiddleware("global"))

    if DISABLE_DIRECT_START:
        dispatcher.update.outer_middleware(wrap(DirectStartBlockerMiddleware(), "direct_start_blocker"))

    if sessionmaker:
        if CHANNEL_REQUIRED:
            dispatcher.update.outer_middleware(wrap(SubscriptionMiddleware(), "subscription"))
        dispatcher.update.outer_middleware(wrap(BanCheckerMiddleware(sessionmaker), "ban_checker"))

    if middlewares is None:
        available_middlewares = {
            "session": (SessionMiddleware(sessionmaker) if sessionmaker else SessionMiddleware()),
            "admin": AdminMiddleware(),
            "maintenance": MaintenanceModeMiddleware(),
            "logging": LoggingMiddleware(),
            "throttling": ThrottlingMiddleware(),
            "user": UserMiddleware(),
            "answer": CallbackAnswerMiddleware(),
        }
        exclude_set = set(exclude or [])
        middlewares = [wrap(mw, name) for name, mw in available_middlewares.items() if name not in exclude_set]
    else:
        wrapped = []
        for mw in middlewares:
            inst = mw() if isinstance(mw, type) else mw
            wrapped.append(wrap(inst, getattr(inst, "name", inst.__class__.__name__)))
        middlewares = wrapped

    handlers = [dispatcher.message, dispatcher.callback_query, dispatcher.inline_query]
    for middleware in middlewares:
        for h in handlers:
            h.outer_middleware(middleware)

    if PROBE_LOGGING:
        for h in handlers:
            h.outer_middleware(TailHandlerProbe("handler"))
