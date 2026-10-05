from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, Update

from core.bootstrap import MODES_CONFIG
from core.redis_cache import cache_get, cache_key, cache_set
from database import check_user_exists
from logger import logger
from settings.cache_config import DIRECT_START_USER_EXISTS_CACHE_TTL_SEC
from settings.config import ADMIN_ID, DISABLE_DIRECT_START


_TTL = DIRECT_START_USER_EXISTS_CACHE_TTL_SEC


class DirectStartBlockerMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self.allowed_prefixes = ("gift_", "referral_", "coupons_", "utm", "partner_")

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        direct_start_disabled = bool(MODES_CONFIG.get("DIRECT_START_DISABLED", DISABLE_DIRECT_START))
        if not direct_start_disabled:
            return await handler(event, data)

        user = getattr(event, "message", None) and getattr(event.message, "from_user", None)
        if not user and getattr(event, "callback_query", None):
            user = getattr(event.callback_query, "from_user", None)
        if user:
            admin_ids = set(ADMIN_ID) if isinstance(ADMIN_ID, list | tuple) else {ADMIN_ID}
            if user.id in admin_ids:
                return await handler(event, data)

        message: Message | None = getattr(event, "message", None)
        if not message or not message.text:
            return await handler(event, data)

        fsm = data.get("state")
        if fsm:
            current_state = await fsm.get_state()
            if current_state:
                return await handler(event, data)

        session = data.get("session")
        if session is None or not hasattr(session, "execute"):
            return await handler(event, data)

        tg_id = message.from_user.id
        text = message.text.strip()
        user_in_data = bool(data.get("user"))

        async def user_exists_cached() -> bool:
            if user_in_data:
                return True

            cached = await cache_get(cache_key("direct_start_user_exists", tg_id))
            if isinstance(cached, bool):
                return cached

            exists = await check_user_exists(session, tg_id)
            await cache_set(cache_key("direct_start_user_exists", tg_id), bool(exists), _TTL)
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

        logger.info(f"[DirectStartBlocker] Разрешённая ссылка от {tg_id}: {start_param!r}")
        return await handler(event, data)
