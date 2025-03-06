from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, Bot
from aiogram.types import TelegramObject, CallbackQuery
from cachetools import TTLCache

THROTTLE_TIME = 1.0


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.cache = TTLCache(maxsize=10_000, ttl=THROTTLE_TIME)
        self.throttle_notice_cache = TTLCache(maxsize=10_000, ttl=3.0)

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any],
    ) -> Any:
        bot: Bot | None = data.get("bot", None)
        user_id = event.from_user.id if event.from_user else None

        if user_id in self.cache:
            if isinstance(event, CallbackQuery) and user_id not in self.throttle_notice_cache:
                self.throttle_notice_cache[user_id] = None
                await bot.answer_callback_query(
                    callback_query_id=event.id,
                    text="Слишком много запросов! Пожалуйста, подождите...",
                    show_alert=False
                )
            return None
        else:
            self.cache[user_id] = None

        return await handler(event, data)
