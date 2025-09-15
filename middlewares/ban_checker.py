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
_BAN_CACHE_TTL = 30
_ban_cache: dict[int, tuple[float, dict | None]] = {}


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

        now_ts = datetime.utcnow().timestamp()
        cached = _ban_cache.get(tg_id)
        if cached and cached[0] > now_ts:
            ban_info = cached[1]
        else:
            session: AsyncSession | None = (
                data.get("session") if isinstance(data.get("session"), AsyncSession) else None
            )
            created_here = False
            if session is None:
                session = self.session_factory()
                created_here = True
            try:
                q = (
                    select(ManualBan.reason, ManualBan.until)
                    .where(
                        ManualBan.tg_id == tg_id,
                        (ManualBan.until.is_(None)) | (ManualBan.until > datetime.utcnow()),
                    )
                    .limit(1)
                )
                res = await session.execute(q)
                row = res.first()
                if row:
                    reason, until = row
                    ban_info = {"reason": reason or "не указана", "until": until}
                else:
                    ban_info = None
            finally:
                if created_here:
                    await session.close()
            _ban_cache[tg_id] = (now_ts + _BAN_CACHE_TTL, ban_info)

        if not ban_info:
            return await handler(event, data)

        reason = ban_info["reason"]
        until = ban_info["until"]

        if reason == "shadow":
            logger.info(f"[BanChecker] Теневой бан: пользователь {tg_id} — действия игнорируются.")
            return

        if until:
            until_local = until.astimezone(TZ).strftime("%Y-%m-%d %H:%M")
            text_html = (
                f"🚫 Вы заблокированы до <b>{until_local}</b> по МСК.\n"
                f"📄 Причина: <i>{reason}</i>\n\n"
                f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
            )
            text_plain = (
                f"🚫 Вы заблокированы до {until_local} по МСК.\n"
                f"📄 Причина: {reason}\n\n"
                f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
            )
        else:
            text_html = (
                f"🚫 Вы заблокированы <b>навсегда</b>.\n"
                f"📄 Причина: <i>{reason}</i>\n\n"
                f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
            )
            text_plain = (
                f"🚫 Вы заблокированы навсегда.\n"
                f"📄 Причина: {reason}\n\n"
                f"Если вы считаете, что это ошибка, обратитесь в поддержку: {SUPPORT_CHAT_URL}"
            )

        if isinstance(obj, Message):
            await obj.answer(text_html, parse_mode="HTML")
        elif isinstance(obj, CallbackQuery):
            await obj.answer(text_plain, show_alert=True)
        return
