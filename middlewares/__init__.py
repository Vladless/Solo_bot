from collections.abc import Iterable

from aiogram import BaseMiddleware, Dispatcher

from core.bootstrap import MODES_CONFIG
from middlewares.ban_checker import BanCheckerMiddleware
from middlewares.subscription import SubscriptionMiddleware

from .actor import ActorMiddleware
from .admin import AdminMiddleware
from .answer import CallbackAnswerMiddleware, EarlyCallbackAnswerMiddleware
from .concurrency import ConcurrencyLimiterMiddleware
from .delete_commands import DeleteCommandMiddleware
from .direct_start_blocker import DirectStartBlockerMiddleware
from .loggings import LoggingMiddleware
from .maintenance import MaintenanceModeMiddleware
from .probe import MiddlewareProbe, StreamProbeMiddleware, TailHandlerProbe
from .runtime_config_sync import RuntimeConfigSyncMiddleware
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

    exclude_set = set(exclude or [])

    flag_by_name = {
        "runtime_config_sync": "RUNTIME_CONFIG_SYNC_MIDDLEWARE_ENABLED",
        "concurrency": "CONCURRENCY_MIDDLEWARE_ENABLED",
        "subscription": "SUBSCRIPTION_MIDDLEWARE_ENABLED",
        "session": "SESSION_MIDDLEWARE_ENABLED",
        "direct_start_blocker": "DIRECT_START_BLOCKER_MIDDLEWARE_ENABLED",
        "ban_checker": "BAN_CHECKER_MIDDLEWARE_ENABLED",
        "admin": "ADMIN_MIDDLEWARE_ENABLED",
        "maintenance": "MAINTENANCE_MIDDLEWARE_ENABLED",
        "logging": "LOGGING_MIDDLEWARE_ENABLED",
        "throttling": "THROTTLING_MIDDLEWARE_ENABLED",
        "user": "USER_MIDDLEWARE_ENABLED",
        "actor": "ACTOR_MIDDLEWARE_ENABLED",
        "answer": "ANSWER_MIDDLEWARE_ENABLED",
        "delete_commands": "DELETE_COMMANDS_MIDDLEWARE_ENABLED",
    }

    def middleware_enabled(name: str) -> bool:
        if name in exclude_set:
            return False
        flag_name = flag_by_name.get(name)
        if not flag_name:
            return True
        return bool(MODES_CONFIG.get(flag_name, True))

    if PROBE_LOGGING:
        dispatcher.update.outer_middleware(StreamProbeMiddleware("global"))

    dispatcher.update.outer_middleware(EarlyCallbackAnswerMiddleware())

    if middleware_enabled("runtime_config_sync"):
        dispatcher.update.outer_middleware(wrap(RuntimeConfigSyncMiddleware(), "runtime_config_sync"))
    if sessionmaker and middleware_enabled("concurrency"):
        dispatcher.update.outer_middleware(wrap(ConcurrencyLimiterMiddleware(), "concurrency"))
    if sessionmaker and middleware_enabled("session"):
        dispatcher.update.outer_middleware(wrap(SessionMiddleware(sessionmaker), "session"))
    if middleware_enabled("ban_checker"):
        dispatcher.update.outer_middleware(wrap(BanCheckerMiddleware(), "ban_checker"))
    if middleware_enabled("direct_start_blocker"):
        dispatcher.update.outer_middleware(wrap(DirectStartBlockerMiddleware(), "direct_start_blocker"))
    if middleware_enabled("admin"):
        dispatcher.update.outer_middleware(wrap(AdminMiddleware(), "admin"))
    if middleware_enabled("maintenance"):
        dispatcher.update.outer_middleware(wrap(MaintenanceModeMiddleware(), "maintenance"))
    if middleware_enabled("subscription"):
        dispatcher.update.outer_middleware(wrap(SubscriptionMiddleware(), "subscription"))

    if middlewares is None:
        available_middlewares = {
            "logging": LoggingMiddleware(sessionmaker) if sessionmaker else LoggingMiddleware(),
            "throttling": ThrottlingMiddleware(),
            "user": UserMiddleware(),
            "actor": ActorMiddleware(),
        }
        middlewares = [wrap(mw, name) for name, mw in available_middlewares.items() if middleware_enabled(name)]
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

    if middleware_enabled("answer"):
        dispatcher.callback_query.middleware(wrap(CallbackAnswerMiddleware(), "answer"))

    if middleware_enabled("delete_commands"):
        dispatcher.message.outer_middleware(wrap(DeleteCommandMiddleware(), "delete_commands"))

    if PROBE_LOGGING:
        for h in handlers:
            h.outer_middleware(TailHandlerProbe("handler"))
