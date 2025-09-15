from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from database import upsert_user
from logger import logger


class UserMiddleware(BaseMiddleware):
    def __init__(self, debounce_sec: float = 60.0) -> None:
        self._debounce = float(debounce_sec)
        self._cache: dict[int, tuple[str, float, dict | None]] = {}

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
                db_user = await self._process_user(user, session)
                if db_user:
                    data["user"] = db_user
        except Exception as e:
            logger.error(f"Ошибка при обработке пользователя: {e}")
        return await handler(event, data)

    async def _process_user(self, user: User, session: Any = None) -> dict | None:
        uid = user.id
        fp = self._fingerprint(user)
        now = monotonic()

        cached = self._cache.get(uid)
        if cached:
            cached_fp, ts, cached_db_user = cached
            if fp == cached_fp and now - ts < self._debounce:
                return cached_db_user

        logger.debug(f"Обработка пользователя: {uid}")
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
        self._cache[uid] = (fp, now, db_user)
        if db_user:
            logger.debug(f"Получены данные пользователя из БД: {uid}")
        return db_user

    def _fingerprint(self, user: User) -> str:
        return "|".join([
            str(user.id),
            user.username or "",
            user.first_name or "",
            user.last_name or "",
            user.language_code or "",
            "1" if user.is_bot else "0",
        ])
