from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, Update
from sqlalchemy import select

from config import DISABLE_DIRECT_START
from database import async_session_maker, check_user_exists
from database.models import Coupon, Gift, TrackingSource, User
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
        logger.debug("[DirectStartBlocker] Middleware triggered")

        if not DISABLE_DIRECT_START:
            return await handler(event, data)

        message: Message | None = getattr(event, "message", None)
        if not message or not message.text:
            return await handler(event, data)

        tg_id = message.from_user.id
        text = message.text.strip()

        async with async_session_maker() as session:
            user_exists = await check_user_exists(session, tg_id)

            if user_exists:
                logger.debug(f"[DirectStartBlocker] Пользователь {tg_id} уже есть в базе — пропущен")
                return await handler(event, data)

            parts = text.split(maxsplit=1)
            if parts[0] == "/start":
                if len(parts) == 1:
                    logger.info(f"[DirectStartBlocker] Прямой старт запрещён для нового пользователя {tg_id}")
                    return

                start_param = parts[1].strip()
                if not start_param or not start_param.startswith(self.allowed_prefixes):
                    logger.info(f"[DirectStartBlocker] Отклонена неизвестная ссылка от {tg_id}: {start_param!r}")
                    return

                if start_param.startswith("coupons_"):
                    code = start_param.removeprefix("coupons_")
                    result = await session.execute(select(Coupon).where(Coupon.code == code))
                    if not result.scalar_one_or_none():
                        logger.info(f"[DirectStartBlocker] Купон не найден: {code!r}")
                        return

                elif start_param.startswith("gift_"):
                    gift_id = start_param.removeprefix("gift_")
                    result = await session.execute(select(Gift).where(Gift.id == gift_id))
                    if not result.scalar_one_or_none():
                        logger.info(f"[DirectStartBlocker] Подарок не найден: {gift_id!r}")
                        return

                elif start_param.startswith("referral_"):
                    try:
                        ref_id = int(start_param.removeprefix("referral_"))
                        result = await session.execute(select(User).where(User.tg_id == ref_id))
                        if not result.scalar_one_or_none():
                            logger.info(f"[DirectStartBlocker] Реферал не найден: {ref_id!r}")
                            return
                    except ValueError:
                        logger.info(f"[DirectStartBlocker] Неверный формат referral-ссылки: {start_param!r}")
                        return

                elif start_param.startswith("utm"):
                    utm_code = start_param
                    result = await session.execute(select(TrackingSource).where(TrackingSource.code == utm_code))
                    if not result.scalar_one_or_none():
                        logger.info(f"[DirectStartBlocker] UTM не найден: {utm_code!r}")
                        return

                logger.info(f"[DirectStartBlocker] Разрешённая и валидная ссылка от {tg_id}: {start_param!r}")
                return await handler(event, data)

        if text.startswith("/"):
            logger.info(
                f"[DirectStartBlocker] Команда '{text}' отклонена для незарегистрированного пользователя {tg_id}"
            )
            return

        return await handler(event, data)
