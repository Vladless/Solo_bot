from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery
from cachetools import TTLCache


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.cache = TTLCache(maxsize=10_000, ttl=1.0)
        self.throttle_notice_cache = TTLCache(maxsize=10_000, ttl=1.0)

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return await handler(event, data)

        current_count = self.cache.get(user_id, 0)

        if current_count >= 3:
            if isinstance(event, CallbackQuery) and user_id not in self.throttle_notice_cache:
                self.throttle_notice_cache[user_id] = None
                bot: Bot = data["bot"]
                await bot.answer_callback_query(
                    callback_query_id=event.id,
                    text="Слишком много запросов! Пожалуйста, подождите...",
                    show_alert=False,
                )
            return None
        else:
            self.cache[user_id] = current_count + 1

        return await handler(event, data)
