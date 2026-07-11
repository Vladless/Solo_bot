from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InaccessibleMessage, TelegramObject

from bot import bot


_HANDLER_ANSWERS_PREFIXES = ("unbind_dev|")


class EarlyCallbackAnswerMiddleware(BaseMiddleware):
    """
    Регистрируется первым в цепочке update. Для CallbackQuery сразу вызывает
    answer_callback_query.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            bot_instance: Bot | None = data.get("bot")
            if bot_instance:
                try:
                    await bot_instance.answer_callback_query(event.id, show_alert=False)
                    data["callback_answered_early"] = True
                except TelegramBadRequest as e:
                    msg = str(e).lower()
                    if "query is too old" in msg or "response timeout expired" in msg or "query id is invalid" in msg:
                        pass
                    else:
                        raise
                except Exception:
                    pass
        return await handler(event, data)


class CallbackAnswerMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            handler_answers = (event.data or "").startswith(_HANDLER_ANSWERS_PREFIXES)
            if (
                not handler_answers
                and not data.get("callback_answered_by_concurrency")
                and not data.get("callback_answered_early")
            ):
                try:
                    await event.answer()
                except Exception:
                    pass
            if isinstance(event.message, InaccessibleMessage):
                try:
                    new_message = await bot.send_message(event.message.chat.id, "⏳")
                    object.__setattr__(event, "message", new_message)
                except Exception:
                    pass
        return await handler(event, data)
