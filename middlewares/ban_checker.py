from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from pytz import timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import SUPPORT_CHAT_URL
from database.models import ManualBan
from logger import logger

TZ = timezone("Europe/Moscow")


class BanCheckerMiddleware(BaseMiddleware):
    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_id = None
        obj = None

        if isinstance(event, Update):
            if event.message:
                tg_id = event.message.from_user.id
                obj = event.message
            elif event.callback_query:
                tg_id = event.callback_query.from_user.id
                obj = event.callback_query
        elif isinstance(event, Message | CallbackQuery):
            tg_id = event.from_user.id
            obj = event

        if tg_id is None:
            return await handler(event, data)

        async with self.session_factory() as session:
            logger.debug(f"[BanChecker] Проверка блокировки для пользователя {tg_id}")
            result = await session.execute(
                select(ManualBan).where(
                    ManualBan.tg_id == tg_id,
                    (ManualBan.until.is_(None)) | (ManualBan.until > datetime.utcnow()),
                )
            )
            ban = result.scalar_one_or_none()

            if ban:
                reason = ban.reason or "не указана"
                until = ban.until

                logger.warning(
                    f"[BanChecker] Пользователь {tg_id} заблокирован (до: {until}, причина: {reason})"
                )

                if until:
                    until_local = until.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
                    text = (
                        f"🚫 Вы заблокированы до <b>{until_local}</b> по МСК.\n"
                        f"📄 Причина: <i>{reason}</i>\n\n"
                        f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
                    )
                else:
                    text = (
                        f"🚫 Вы заблокированы <b>навсегда</b>.\n"
                        f"📄 Причина: <i>{reason}</i>\n\n"
                        f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
                    )

                if isinstance(obj, Message):
                    await obj.answer(text, parse_mode="HTML")
                elif isinstance(obj, CallbackQuery):
                    await obj.answer(text, show_alert=True)
                return
        return await handler(event, data)
