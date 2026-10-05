from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from database.access.resolution import resolve_actor_from_legacy_ref
from logger import logger


class ActorMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            from_user: User | None = data.get("event_from_user")
            session = data.get("session")
            if (
                from_user is not None
                and not from_user.is_bot
                and session is not None
                and getattr(session, "execute", None) is not None
            ):
                data["actor"] = await resolve_actor_from_legacy_ref(session, int(from_user.id))
        except Exception as error:
            logger.error(f"[ActorMiddleware] Ошибка резолва actor: {error}", exc_info=True)
            _session = data.get("session")
            if _session is not None:
                try:
                    await _session.rollback()
                except Exception:
                    pass
        return await handler(event, data)
