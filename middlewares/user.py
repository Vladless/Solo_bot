from collections.abc import Awaitable, Callable
from datetime import datetime
from time import time
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_get, cache_key, cache_set
from database import upsert_user
from database.models import User as DbUser
from logger import logger


class UserMiddleware(BaseMiddleware):
    def __init__(self, debounce_sec: float = 60.0, cache_maxsize: int = 100_000) -> None:
        self._debounce = float(debounce_sec)
        self._cache_ttl = debounce_sec * 2

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            user: User | None = data.get("event_from_user")
            if user and not user.is_bot:
                session = data.get("session")
                if session is not None and getattr(session, "execute", None) is not None:
                    db_user = await self._process_user(user, session)
                    if db_user:
                        data["user"] = db_user
        except Exception as e:
            logger.error(f"Ошибка при обработке пользователя: {e}", exc_info=True)
            _session = data.get("session")
            if _session is not None:
                try:
                    await _session.rollback()
                except Exception:
                    pass
        return await handler(event, data)

    async def _process_user(self, user: User, session: AsyncSession) -> dict | None:
        uid = user.id
        fingerprint = self._fingerprint(user)
        now = time()
        key = cache_key("user_middleware", uid)

        cached = await cache_get(key)
        if isinstance(cached, dict):
            cached_fingerprint = str(cached.get("fingerprint") or "")
            profile_ts = float(cached.get("profile_ts") or 0.0)
            touch_ts = float(cached.get("touch_ts") or 0.0)
            cached_db_user = cached.get("db_user")

            if fingerprint == cached_fingerprint:
                if now - touch_ts >= self._debounce:
                    db_user = await self._touch_user(uid, session)
                    await cache_set(
                        key,
                        {
                            "fingerprint": cached_fingerprint,
                            "profile_ts": profile_ts,
                            "touch_ts": now,
                            "db_user": db_user or cached_db_user,
                        },
                        self._cache_ttl,
                    )
                    return db_user or cached_db_user

                if now - profile_ts < self._debounce:
                    return cached_db_user

        db_user = await upsert_user(
            tg_id=uid,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
            is_bot=user.is_bot,
            session=session,
            only_if_exists=True,
        )
        await cache_set(
            key,
            {
                "fingerprint": fingerprint,
                "profile_ts": now,
                "touch_ts": now,
                "db_user": db_user,
            },
            self._cache_ttl,
        )
        return db_user

    async def _touch_user(self, tg_id: int, session: AsyncSession) -> dict | None:
        now = datetime.utcnow()
        res = await session.execute(
            update(DbUser).where(DbUser.tg_id == tg_id).values(updated_at=now).returning(DbUser)
        )
        obj = res.scalar_one_or_none()
        if obj is None:
            return None
        d = obj.__dict__.copy()
        d.pop("_sa_instance_state", None)
        return d

    def _fingerprint(self, user: User) -> str:
        return "|".join([
            str(user.id),
            user.username or "",
            user.first_name or "",
            user.last_name or "",
            user.language_code or "",
            "1" if user.is_bot else "0",
        ])
