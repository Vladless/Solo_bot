from hashlib import sha1

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from core.rate_limit import rate_limit_hit
from core.redis_cache import cache_incr, cache_key, cache_setnx
from logger import logger
from settings.cache_config import (
    THROTTLE_CACHE_TTL_SEC,
    THROTTLE_MESSAGE_LIMIT,
    THROTTLE_MESSAGE_NOTICE_TTL_SEC,
    THROTTLE_MESSAGE_WINDOW_SEC,
    THROTTLE_NOTICE_TTL_SEC,
)


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._counter_ttl = THROTTLE_CACHE_TTL_SEC
        self._notice_ttl = THROTTLE_NOTICE_TTL_SEC

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            if await self._message_flood(event, data):
                return None
            return await handler(event, data)

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

    async def _message_flood(self, event: Message, data) -> bool:
        """Частота сообщений на пользователя. Апдейт сверх лимита не обрабатываем."""
        user_id = event.from_user.id if event.from_user else None
        if user_id is None:
            return False

        count, exceeded = await rate_limit_hit(
            cache_key("throttle_msg", user_id), THROTTLE_MESSAGE_LIMIT, THROTTLE_MESSAGE_WINDOW_SEC
        )
        if not exceeded:
            return False

        notice_key = cache_key("throttle_msg_notice", user_id)
        if await cache_setnx(notice_key, 1, THROTTLE_MESSAGE_NOTICE_TTL_SEC):
            logger.warning("[Throttle] Пользователь {} превысил лимит сообщений: {}", user_id, count)
            bot: Bot = data.get("bot")
            if bot is not None and event.chat is not None:
                try:
                    await bot.send_message(event.chat.id, "Слишком много сообщений. Подождите немного.")
                except Exception as exc:
                    logger.debug("[Throttle] Не удалось предупредить {}: {}", user_id, exc)
        return True
