from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, Update

from config import DISABLE_DIRECT_START
from database import check_user_exists, async_session_maker
from logger import logger


class DirectStartBlockerMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.allowed_prefixes = ("gift_", "referral_", "coupons_", "utm", "partner_")

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        if not DISABLE_DIRECT_START:
            return await handler(event, data)

        if not isinstance(event, Message):
            return await handler(event, data)

        message: Message = event
        if not message.text:
            return await handler(event, data)

        tg_id = message.from_user.id
        text = message.text.strip()

        async with async_session_maker() as session:
            user_exists = await check_user_exists(session, tg_id)

        if user_exists:
            logger.debug(f"[DirectStartBlocker] Пользователь {tg_id} уже есть в базе — пропущен")
            return await handler(event, data)

        parts = text.split(maxsplit=1)

        if parts[0] != "/start":
            return await handler(event, data)

        if len(parts) == 1:
            logger.info(f"[DirectStartBlocker] Прямой старт запрещён для нового пользователя {tg_id}")
            return

        start_param = parts[1].strip()
        if not start_param or not start_param.startswith(self.allowed_prefixes):
            logger.info(f"[DirectStartBlocker] Отклонена неизвестная ссылка от {tg_id}: {start_param!r}")
            return

        logger.debug(f"[DirectStartBlocker] Разрешённая ссылка от {tg_id}: {start_param!r}")
        return await handler(event, data)
