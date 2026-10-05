from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from database.access.resolution import user_id_from_legacy_ref
from logger import logger


class LegacyUserRefMiddleware(BaseMiddleware):
    """Приводит идентификатор клиента в callback-данных к users.id."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        callback_data = data.get("callback_data")
        session = data.get("session")
        ref = getattr(callback_data, "user_id", None)
        if isinstance(ref, int) and session is not None and getattr(session, "scalar", None) is not None:
            try:
                resolved = await user_id_from_legacy_ref(session, ref)
                if resolved is not None and resolved != ref:
                    data["callback_data"] = callback_data.model_copy(update={"user_id": resolved})
                    logger.debug("[LegacyUserRef] {} -> users.id={}", ref, resolved)
            except Exception as error:
                logger.error(f"[LegacyUserRef] Ошибка резолва {ref}: {error}", exc_info=True)
        return await handler(event, data)
