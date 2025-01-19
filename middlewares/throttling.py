from typing import Any, Awaitable, Callable, MutableMapping

from aiogram import BaseMiddleware
from aiogram.dispatcher.flags import get_flag
from aiogram.types import TelegramObject, Update, User
from cachetools import TTLCache

from logger import logger

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(
        self,
        *,
        default_key: str | None = "default",
        default_ttl: float = 0.5,
        **ttl_map: float,
    ) -> None:
        if default_key:
            ttl_map[default_key] = default_ttl

        self.default_key = default_key
        self.caches: dict[str, MutableMapping[int, None]] = {}

        for name, ttl in ttl_map.items():
            self.caches[name] = TTLCache(maxsize=10_000, ttl=ttl)
        logger.debug("ThrottlingMiddleware initialized.")

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            logger.debug(f"Received event of type {type(event)}, skipping throttling.")
            return await handler(event, data)

        if event.pre_checkout_query:
            logger.debug("Pre-checkout query event, skipping throttling.")
            return await handler(event, data)

        if event.message and event.message.successful_payment:
            logger.debug("Successful payment event, skipping throttling.")
            return await handler(event, data)

        user: User | None = data.get("event_from_user", None)

        if user is not None:
            key = get_flag(data, "throttling_key", default=self.default_key)

            if key:
                if user.id in self.caches[key]:
                    logger.warning(f"User {user.id} is being throttled with key: {key}")
                    return None
                logger.debug(
                    f"User {user.id} is allowed to proceed, adding to cache with key: {key}",
                )
                self.caches[key][user.id] = None
            else:
                logger.debug(
                    f"No throttling key provided for user {user.id}, proceeding without throttle."
                )

        return await handler(event, data)