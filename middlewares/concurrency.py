import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject

from database.db import CONCURRENT_UPDATES_LIMIT, MAX_UPDATE_AGE_SEC


class ConcurrencyLimiterMiddleware(BaseMiddleware):
    """
    Регистрируется до SessionMiddleware. Ограничивает число апдейтов, одновременно
    получающих сессию, и отсекает апдейты, ждавшие слишком долго.
    """

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(CONCURRENT_UPDATES_LIMIT)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["request_time"] = time.monotonic()
        await self._semaphore.acquire()
        try:
            age = time.monotonic() - data["request_time"]
            if age > MAX_UPDATE_AGE_SEC:
                await self._reject_stale(event, data)
                return None
            return await handler(event, data)
        finally:
            self._semaphore.release()

    async def _reject_stale(self, event: TelegramObject, data: dict[str, Any]) -> None:
        if isinstance(event, CallbackQuery):
            bot: Bot = data.get("bot")
            if bot:
                try:
                    await bot.answer_callback_query(
                        event.id,
                        text="Время ожидания истекло. Нажмите ещё раз.",
                        show_alert=False,
                    )
                except Exception:
                    pass
        elif isinstance(event, Message) and event.text and event.chat:
            bot: Bot = data.get("bot")
            if bot:
                try:
                    await bot.send_message(
                        event.chat.id,
                        "Сейчас высокая нагрузка. Отправьте команду ещё раз через пару секунд.",
                    )
                except Exception:
                    pass
