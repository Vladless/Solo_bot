from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_get, cache_key, cache_set
from database.models import Admin
from settings.cache_config import ADMIN_CACHE_TTL_SEC
from settings.config import ADMIN_ID


_ADMIN_CACHE_TTL = ADMIN_CACHE_TTL_SEC


class AdminMiddleware(BaseMiddleware):
    """Проверяет, является ли пользователь администратором. Сессию не создаёт — только data['session']."""

    _admin_ids: set[int] = set(ADMIN_ID) if isinstance(ADMIN_ID, list | tuple) else {ADMIN_ID}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        data["admin"] = await self._check_admin_access(event, session)
        return await handler(event, data)

    async def _check_admin_access(
        self,
        event: TelegramObject,
        session: AsyncSession | None,
    ) -> bool:
        try:
            source = event
            if isinstance(event, Update):
                source = event.message or event.callback_query or event.inline_query

            from_user = getattr(source, "from_user", None)
            user_id = getattr(from_user, "id", None) if from_user else None

            if not user_id:
                return False

            if user_id in self._admin_ids:
                return True

            cached = await cache_get(cache_key("admin_access", user_id))
            if isinstance(cached, bool):
                return cached

            if not session:
                await cache_set(cache_key("admin_access", user_id), False, _ADMIN_CACHE_TTL)
                return False

            result = await session.execute(select(Admin).where(Admin.tg_id == user_id))
            is_admin = result.scalar_one_or_none() is not None
            await cache_set(cache_key("admin_access", user_id), bool(is_admin), _ADMIN_CACHE_TTL)
            return is_admin
        except Exception:
            if session is not None:
                try:
                    await session.rollback()
                except Exception:
                    pass
            return False
