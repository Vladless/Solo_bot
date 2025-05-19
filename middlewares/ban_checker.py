from typing import Callable, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from asyncpg import Pool
from pytz import timezone

from config import SUPPORT_CHAT_URL


TZ = timezone("Europe/Moscow")  # или другая зона, если нужно

class BanCheckerMiddleware(BaseMiddleware):
    def __init__(self, pool: Pool):
        self.pool = pool

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        tg_id = (
            event.from_user.id
            if isinstance(event, (Message, CallbackQuery))
            else None
        )
        if tg_id is None:
            return await handler(event, data)

        async with self.pool.acquire() as conn:
            record = await conn.fetchrow(
                """
                SELECT until FROM manual_bans
                WHERE tg_id = $1 AND (until IS NULL OR until > NOW())
                """,
                tg_id,
            )

        if record:
            until = record["until"]
            if until:
                until_local = until.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
                text = (
                    f"🚫 Вы заблокированы до <b>{until_local}</b> по МСК.\n"
                    f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
                )
            else:
                text = (
                    f"🚫 Вы заблокированы <b>навсегда</b>.\n"
                    f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
                )

            if isinstance(event, Message):
                await event.answer(text, parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            return

        return await handler(event, data)
