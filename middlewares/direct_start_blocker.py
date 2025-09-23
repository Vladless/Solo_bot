import time

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, Update
from sqlalchemy import select

from config import DISABLE_DIRECT_START
from database import async_session_maker, check_user_exists
from database.models import Coupon, Gift, TrackingSource, User
from logger import logger


_TTL = 20
_cache_user_exists: dict[int, tuple[float, bool]] = {}
_cache_coupon: dict[str, tuple[float, bool]] = {}
_cache_gift: dict[str, tuple[float, bool]] = {}
_cache_ref: dict[int, tuple[float, bool]] = {}
_cache_utm: dict[str, tuple[float, bool]] = {}


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

        message: Message | None = getattr(event, "message", None)
        if not message or not message.text:
            return await handler(event, data)

        fsm = data.get("state")
        if fsm:
            current_state = await fsm.get_state()
            if current_state:
                return await handler(event, data)

        tg_id = message.from_user.id
        text = message.text.strip()
        now = time.time()

        async def user_exists_cached() -> bool:
            cached = _cache_user_exists.get(tg_id)
            if cached and cached[0] > now:
                return cached[1]
            async with async_session_maker() as session:
                exists = await check_user_exists(session, tg_id)
            _cache_user_exists[tg_id] = (now + _TTL, exists)
            return exists

        if not text.startswith("/"):
            if await user_exists_cached():
                return await handler(event, data)
            return

        parts = text.split(maxsplit=1)
        if parts[0] != "/start":
            if await user_exists_cached():
                return await handler(event, data)
            logger.info(
                f"[DirectStartBlocker] Команда '{text}' отклонена для незарегистрированного пользователя {tg_id}"
            )
            return

        if len(parts) == 1:
            if await user_exists_cached():
                return await handler(event, data)
            logger.info(f"[DirectStartBlocker] Прямой старт запрещён для нового пользователя {tg_id}")
            return

        start_param = parts[1].strip()
        if not start_param or not start_param.startswith(self.allowed_prefixes):
            if await user_exists_cached():
                return await handler(event, data)
            logger.info(f"[DirectStartBlocker] Отклонена неизвестная ссылка от {tg_id}: {start_param!r}")
            return

        async with async_session_maker() as session:
            if start_param.startswith("coupons_"):
                code = start_param.removeprefix("coupons_")
                cached = _cache_coupon.get(code)
                if cached and cached[0] > now:
                    ok = cached[1]
                else:
                    ok = (
                        await session.execute(select(Coupon.id).where(Coupon.code == code).limit(1))
                    ).first() is not None
                    _cache_coupon[code] = (now + _TTL, ok)
                if not ok:
                    logger.info(f"[DirectStartBlocker] Купон не найден: {code!r}")
                    return

            elif start_param.startswith("gift_"):
                gift_id = start_param.removeprefix("gift_")
                cached = _cache_gift.get(gift_id)
                if cached and cached[0] > now:
                    ok = cached[1]
                else:
                    ok = (
                        await session.execute(
                            select(Gift.gift_id)
                            .where(
                                Gift.gift_id == gift_id,
                                Gift.is_used.is_(False),
                                (Gift.expiry_time.is_(None)) | (Gift.expiry_time > datetime.utcnow()),
                            )
                            .limit(1)
                        )
                    ).first() is not None
                    _cache_gift[gift_id] = (now + _TTL, ok)
                if not ok:
                    logger.info(f"[DirectStartBlocker] Подарок неактивен или не найден: {gift_id!r}")
                    return

            elif start_param.startswith("referral_"):
                try:
                    ref_id = int(start_param.removeprefix("referral_"))
                except ValueError:
                    logger.info(f"[DirectStartBlocker] Неверный формат referral-ссылки: {start_param!r}")
                    return
                cached = _cache_ref.get(ref_id)
                if cached and cached[0] > now:
                    ok = cached[1]
                else:
                    ok = (
                        await session.execute(select(User.tg_id).where(User.tg_id == ref_id).limit(1))
                    ).first() is not None
                    _cache_ref[ref_id] = (now + _TTL, ok)
                if not ok:
                    logger.info(f"[DirectStartBlocker] Реферал не найден: {ref_id!r}")
                    return

            elif start_param.startswith("utm"):
                utm_code = start_param
                cached = _cache_utm.get(utm_code)
                if cached and cached[0] > now:
                    ok = cached[1]
                else:
                    ok = (
                        await session.execute(
                            select(TrackingSource.code).where(TrackingSource.code == utm_code).limit(1)
                        )
                    ).first() is not None
                    _cache_utm[utm_code] = (now + _TTL, ok)
                if not ok:
                    logger.info(f"[DirectStartBlocker] UTM не найден: {utm_code!r}")
                    return

        logger.info(f"[DirectStartBlocker] Разрешённая и валидная ссылка от {tg_id}: {start_param!r}")
        return await handler(event, data)
