from hashlib import sha1

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from core.redis_cache import cache_incr, cache_key, cache_setnx
from settings.cache_config import (
    THROTTLE_CACHE_TTL_SEC,
    THROTTLE_NOTICE_TTL_SEC,
)


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._counter_ttl = THROTTLE_CACHE_TTL_SEC
        self._notice_ttl = THROTTLE_NOTICE_TTL_SEC

    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return await handler(event, data)

        key = (user_id, event.data or "")
        key_hash = sha1(key[1].encode("utf-8")).hexdigest()
        counter_key = cache_key("throttle_counter", user_id, key_hash)
        current_count = await cache_incr(counter_key, self._counter_ttl)

        if current_count >= 2:
            notice_key = cache_key("throttle_notice", user_id, key_hash)
            if await cache_setnx(notice_key, 1, self._notice_ttl):
                bot: Bot = data["bot"]
                try:
                    await bot.answer_callback_query(
                        callback_query_id=event.id,
                        text="Слишком много нажатий, подождите...",
                        show_alert=False,
                    )
                except TelegramBadRequest as e:
                    msg = str(e).lower()
                    if "query is too old" in msg or "response timeout expired" in msg or "query id is invalid" in msg:
                        pass
                    else:
                        raise
            return

        return await handler(event, data)
