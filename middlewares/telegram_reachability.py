import asyncio

from time import perf_counter
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, Update
from sqlalchemy import text

from database import (
    async_session_maker,
    bans as chat_availability,
)
from logger import logger


class TelegramReachabilityMiddleware(BaseMiddleware):
    def __init__(self, *, sessionmaker=None) -> None:
        self._sessionmaker = sessionmaker or async_session_maker

    async def __call__(self, handler, event, data) -> Any:
        message = event.message if isinstance(event, Update) else event if isinstance(event, Message) else None
        if (
            message is not None
            and message.chat.type == "private"
            and message.chat.id > 0
            and message.from_user is not None
            and not message.from_user.is_bot
            and message.from_user.id == message.chat.id
        ):
            await self._confirm_private_message(message)
        return await handler(event, data)

    async def _confirm_private_message(self, message: Message) -> None:
        """Подтверждает доступность чата отдельной короткой транзакцией."""
        started = perf_counter()
        try:
            async with asyncio.timeout(2):
                async with self._sessionmaker() as session:
                    async with session.begin():
                        if session.bind is not None and session.bind.dialect.name == "postgresql":
                            await session.execute(text("SET LOCAL lock_timeout = '1s'"))
                        await chat_availability.apply_telegram_chat_observations(
                            session, [(None, message.chat.id, False, message.date)]
                        )
        except Exception as error:
            logger.exception(
                "[TelegramReachability] Ошибка metadata: chat={}, type={}, elapsed_ms={:.1f}",
                message.chat.id,
                type(error).__name__,
                (perf_counter() - started) * 1000,
            )
