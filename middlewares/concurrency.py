import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from database.db import (
    CONCURRENT_UPDATES_GATE_LIMIT,
    CONCURRENT_UPDATES_GATE_WAIT_SEC,
    CONCURRENT_UPDATES_LIMIT,
    CONCURRENT_UPDATES_WAIT_TIMEOUT_SEC,
    MAX_UPDATE_AGE_SEC,
)
from logger import logger


class ConcurrencyLimiterMiddleware(BaseMiddleware):
    """
    Регистрируется до SessionMiddleware. Шлюз (gate) ограничивает число апдейтов
    в конвейере; семафор — число одновременно обрабатываемых с БД. Лишние
    апдейты сразу получают «высокая нагрузка» и не создают тысячи ожидающих задач.
    """

    def __init__(self) -> None:
        self._gate = asyncio.Semaphore(CONCURRENT_UPDATES_GATE_LIMIT)
        self._semaphore = asyncio.Semaphore(CONCURRENT_UPDATES_LIMIT)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["request_time"] = time.monotonic()
        if isinstance(event, CallbackQuery):
            await self._answer_callback_early(event, data)
        gate_wait = CONCURRENT_UPDATES_GATE_WAIT_SEC if CONCURRENT_UPDATES_GATE_WAIT_SEC else 0
        try:
            await asyncio.wait_for(self._gate.acquire(), timeout=gate_wait)
        except asyncio.TimeoutError:
            logger.warning("[Concurrency] Reject: gate full (очередь переполнена)")
            await self._reject_overload(event, data)
            return None
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=CONCURRENT_UPDATES_WAIT_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                logger.warning("[Concurrency] Reject: semaphore timeout (все слоты БД заняты)")
                await self._reject_overload(event, data)
                return None
            try:
                age = time.monotonic() - data["request_time"]
                if age > MAX_UPDATE_AGE_SEC:
                    logger.warning("[Concurrency] Reject: update too old (age %.1fs)", age)
                    await self._reject_stale(event, data)
                    return None
                return await handler(event, data)
            finally:
                self._semaphore.release()
        finally:
            self._gate.release()

    async def _answer_callback_early(self, event: CallbackQuery, data: dict[str, Any]) -> None:
        """Отвечает на callback сразу, снимая таймаут «устаревший запрос» при долгой очереди."""
        if data.get("callback_answered_early"):
            return
        bot: Bot = data.get("bot")
        if not bot:
            return
        try:
            await bot.answer_callback_query(
                event.id,
                text="⏳",
                show_alert=False,
            )
            data["callback_answered_early"] = True
        except Exception:
            pass

    async def _reject_stale(self, event: TelegramObject, data: dict[str, Any]) -> None:
        await self._send_reject_message(event, data)

    async def _reject_overload(self, event: TelegramObject, data: dict[str, Any]) -> None:
        await self._send_reject_message(event, data)

    async def _send_reject_message(self, event: TelegramObject, data: dict[str, Any]) -> None:
        """Отправляет пользователю сообщение «высокая нагрузка / нажмите ещё раз»."""
        bot: Bot = data.get("bot")
        if not bot:
            return
        text = "Сейчас высокая нагрузка. Попробуйте ещё раз через несколько секунд."
        try:
            if isinstance(event, Update):
                chat_id, callback = self._chat_and_callback_from_update(event)
                if chat_id is None:
                    return
                if callback and not data.get("callback_answered_early"):
                    await bot.answer_callback_query(
                        callback.id,
                        text="Время ожидания истекло. Нажмите ещё раз.",
                        show_alert=False,
                    )
                else:
                    await bot.send_message(chat_id, text)
            elif isinstance(event, CallbackQuery):
                if data.get("callback_answered_early"):
                    if event.message and event.message.chat:
                        await bot.send_message(event.message.chat.id, text)
                else:
                    await bot.answer_callback_query(
                        event.id,
                        text="Время ожидания истекло. Нажмите ещё раз.",
                        show_alert=False,
                    )
            elif isinstance(event, Message) and event.chat:
                await bot.send_message(event.chat.id, text)
        except Exception:
            pass

    @staticmethod
    def _chat_and_callback_from_update(update: Update) -> tuple[int | None, CallbackQuery | None]:
        """Извлекает chat_id и callback (если есть) из Update для отправки сообщения."""
        if update.message and update.message.chat:
            return update.message.chat.id, None
        if update.callback_query and update.callback_query.message and update.callback_query.message.chat:
            return update.callback_query.message.chat.id, update.callback_query
        return None, None
