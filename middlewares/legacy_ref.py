from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from database.access.resolution import AmbiguousUserRef, parse_user_ref, resolve_admin_user_ref
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
        raw = getattr(event, "data", "") or ""
        if ref is None and raw.split("|", 1)[0] in {
            "confirm_admin_key_reissue",
            "admin_key_reissue",
            "admin_reissue_country",
            "admin_reissue_server",
            "confirm_recreate",
            "user_gift_page",
            "user_gift_del",
            "user_gift_del_c",
        }:
            try:
                ref = parse_user_ref(raw.split("|")[1])
            except (ValueError, IndexError):
                await event.answer("Кнопка устарела. Откройте карточку клиента заново.", show_alert=True)
                return
        if isinstance(ref, int) and session is not None and getattr(session, "scalar", None) is not None:
            try:
                resolved = await resolve_admin_user_ref(session, ref)
                if resolved is None:
                    await event.answer("Клиент не найден. Откройте карточку заново.", show_alert=True)
                    return
                if callback_data is not None:
                    data["callback_data"] = callback_data.model_copy(update={"user_id": resolved})
                data["admin_user_ref"] = resolved
            except AmbiguousUserRef:
                await event.answer("Кнопка устарела. Откройте карточку клиента заново.", show_alert=True)
                return
            except Exception as error:
                logger.error(f"[LegacyUserRef] Ошибка резолва {ref}: {error}", exc_info=True)
                await event.answer("Не удалось определить клиента. Попробуйте снова.", show_alert=True)
                return
        return await handler(event, data)
