from collections.abc import Awaitable, Callable
from typing import Any, Dict

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, TelegramObject
from cachetools import TTLCache


THROTTLE_TIME = 1.0


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.cache = TTLCache(maxsize=10_000, ttl=THROTTLE_TIME)
        self.throttle_notice_cache = TTLCache(maxsize=10_000, ttl=3.0)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot | None = data.get("bot", None)
        user_id = event.from_user.id if event.from_user else None

        if user_id in self.cache:
            if isinstance(event, CallbackQuery) and user_id not in self.throttle_notice_cache:
                self.throttle_notice_cache[user_id] = None
                await bot.answer_callback_query(
                    callback_query_id=event.id,
                    text="Слишком много запросов! Пожалуйста, подождите...",
                    show_alert=False,
                )
            return None
        else:
            self.cache[user_id] = None

        return await handler(event, data)
