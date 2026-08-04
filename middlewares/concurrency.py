import asyncio
import time

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, TelegramObject

from core.redis_cache import cache_key, cache_setnx
from settings.cache_config import (
    CONCURRENCY_LIMIT,
    CONCURRENCY_MAX_WAIT_SEC,
    CONCURRENCY_REJECT_NOTICE_TTL_SEC,
)


class ConcurrencyLimiterMiddleware(BaseMiddleware):
    """
    Регистрируется до SessionMiddleware. Ограничивает число апдейтов, одновременно
    получающих сессию (CONCURRENCY_LIMIT), чтобы не упираться в лимит.
    Остальные ждут в очереди до CONCURRENCY_MAX_WAIT_SEC; по истечении — вежливый отказ.
    """

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        self._notice_ttl = CONCURRENCY_REJECT_NOTICE_TTL_SEC
        self._max_wait_sec = CONCURRENCY_MAX_WAIT_SEC

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery) and not data.get("callback_answered_early"):
            bot: Bot | None = data.get("bot")
            if bot:
                try:
                    await bot.answer_callback_query(
                        event.id,
                        text="Подождите…",
                        show_alert=False,
                    )
                    data["callback_answered_by_concurrency"] = True
                except TelegramBadRequest as e:
                    msg = str(e).lower()
                    if "query is too old" in msg or "response timeout expired" in msg or "query id is invalid" in msg:
                        pass
                    else:
                        raise
                except Exception:
                    pass

        data["request_time"] = time.monotonic()
        await self._semaphore.acquire()
        try:
            age = time.monotonic() - data["request_time"]
            if age > self._max_wait_sec:
                await self._reject_stale(event, data)
                return None
            return await handler(event, data)
        finally:
            self._semaphore.release()

    async def _reject_stale(self, event: TelegramObject, data: dict[str, Any]) -> None:
        if isinstance(event, CallbackQuery):
            bot: Bot = data.get("bot")
            uid = event.from_user.id if event.from_user else None
            should_notify = (
                bot and uid is not None and await cache_setnx(cache_key("concurrency_notice", uid), 1, self._notice_ttl)
            )
            if should_notify and event.message and getattr(event.message, "chat", None):
                try:
                    await bot.send_message(
                        event.message.chat.id,
                        "Очередь переполнена. Подождите 1–2 минуты и нажмите снова.",
                    )
                except Exception:
                    pass
        elif isinstance(event, Message) and event.text and event.chat:
            bot: Bot = data.get("bot")
            uid = event.from_user.id if event.from_user else None
            should_notify = (
                bot and uid is not None and await cache_setnx(cache_key("concurrency_notice", uid), 1, self._notice_ttl)
            )
            if should_notify:
                try:
                    await bot.send_message(
                        event.chat.id,
                        "Сейчас много запросов. Вы в очереди — подождите 1–2 минуты и попробуйте снова.",
                    )
                except Exception:
                    pass
