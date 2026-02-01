from collections.abc import Awaitable, Callable
from datetime import datetime
from time import monotonic
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from database import upsert_user
from database.models import User as DbUser
from logger import logger


class UserMiddleware(BaseMiddleware):
    def __init__(self, debounce_sec: float = 60.0) -> None:
        self._debounce = float(debounce_sec)
        self._cache: dict[int, tuple[str, float, float, dict | None]] = {}

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
                if isinstance(session, AsyncSession):
                    db_user = await self._process_user(user, session)
                    if db_user:
                        data["user"] = db_user
        except Exception as e:
            logger.error(f"Ошибка при обработке пользователя: {e}")
        return await handler(event, data)

    async def _process_user(self, user: User, session: AsyncSession) -> dict | None:
        uid = user.id
        fingerprint = self._fingerprint(user)
        now = monotonic()

        cached = self._cache.get(uid)
        if cached:
            cached_fingerprint, profile_ts, touch_ts, cached_db_user = cached

            if fingerprint == cached_fingerprint:
                if now - touch_ts >= self._debounce:
                    db_user = await self._touch_user(uid, session)
                    self._cache[uid] = (cached_fingerprint, profile_ts, now, db_user or cached_db_user)
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
        self._cache[uid] = (fingerprint, now, now, db_user)
        return db_user

    async def _touch_user(self, tg_id: int, session: AsyncSession) -> dict | None:
        now = datetime.utcnow()
        res = await session.execute(
            update(DbUser).where(DbUser.tg_id == tg_id).values(updated_at=now).returning(DbUser)
        )
        obj = res.scalar_one_or_none()
        if obj is None:
            return None
        await session.commit()
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
