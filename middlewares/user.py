import asyncio

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from time import perf_counter, time
from typing import Any
from uuid import uuid4
from weakref import WeakValueDictionary

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User
from cachetools import TTLCache
from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_compare_and_delete, cache_get, cache_key, cache_set, cache_setnx
from database import async_session_maker, upsert_user
from database.models import User as DbUser
from logger import logger
from settings.cache_config import USER_EXISTS_CACHE_TTL_SEC


_PROFILE_TIMEOUT_SEC = 3.0
_LEASE_CLEANUP_TIMEOUT_SEC = 0.25


class UserMiddleware(BaseMiddleware):
    def __init__(
        self,
        debounce_sec: float = 60.0,
        cache_maxsize: int = 100_000,
        *,
        sessionmaker=None,
    ) -> None:
        self._debounce = max(0.0, float(debounce_sec))
        self._cache_ttl = max(1.0, self._debounce * 2)
        self._sessionmaker = sessionmaker or async_session_maker
        self._cache = TTLCache(maxsize=max(1, cache_maxsize), ttl=self._cache_ttl)
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update) and not (event.message or event.callback_query or event.inline_query):
            return await handler(event, data)
        user: User | None = data.get("event_from_user")
        if user and not user.is_bot:
            started = perf_counter()
            try:
                async with asyncio.timeout(_PROFILE_TIMEOUT_SEC):
                    db_user = await self._process_user(user)
                if db_user:
                    data["user"] = db_user
            except Exception as error:
                logger.exception(
                    "[UserMiddleware] Ошибка обновления профиля: tg_id={}, type={}, elapsed_ms={:.1f}",
                    user.id,
                    type(error).__name__,
                    (perf_counter() - started) * 1000,
                )
        return await handler(event, data)

    async def _process_user(self, user: User) -> dict | None:
        uid = user.id
        fingerprint = self._fingerprint(user)
        key = cache_key("user_middleware", uid)
        lock = self._locks.get(uid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[uid] = lock

        async with lock:
            now = time()
            cached = self._cache.get(uid)
            if cached is None:
                cached = await cache_get(key)
            cached_db_user = self._decode_user(cached.get("db_user")) if isinstance(cached, dict) else None
            if (
                cached_db_user
                and cached_db_user.get("tg_id") == uid
                and cached.get("fingerprint") == fingerprint
                and now - float(cached.get("touch_ts") or 0.0) < self._debounce
            ):
                self._cache[uid] = cached
                return cached_db_user

            lease_key = cache_key("user_middleware_lock", uid)
            token = uuid4().hex
            acquired = await cache_setnx(lease_key, token, 5)
            if not acquired and await cache_get(lease_key) is not None:
                return cached_db_user if cached_db_user and cached_db_user.get("tg_id") == uid else None
            try:
                db_user = await self._load_user(user)
                if db_user is None:
                    return None
                now = time()
                updated_at = db_user.get("updated_at")
                if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=UTC)
                touch_ts = updated_at.timestamp() if isinstance(updated_at, datetime) else now
                payload = {
                    "fingerprint": fingerprint,
                    "profile_ts": now,
                    "touch_ts": min(now, touch_ts),
                    "db_user": {
                        name: value.isoformat() if isinstance(value, datetime) else value
                        for name, value in db_user.items()
                    },
                }
                self._cache[uid] = payload
                await cache_set(key, payload, self._cache_ttl)
                await cache_set(cache_key("user_exists", uid), True, USER_EXISTS_CACHE_TTL_SEC)
                return db_user
            finally:
                if acquired:
                    try:
                        async with asyncio.timeout(_LEASE_CLEANUP_TIMEOUT_SEC):
                            await cache_compare_and_delete(lease_key, token)
                    except Exception as error:
                        logger.warning(
                            "[UserMiddleware] Аренда профиля оставлена до TTL: tg_id={}, type={}",
                            uid,
                            type(error).__name__,
                        )

    async def _load_user(self, user: User) -> dict | None:
        """Обновляет профиль в ограниченной по времени отдельной транзакции."""
        started = perf_counter()
        try:
            async with asyncio.timeout(2):
                async with self._sessionmaker() as session:
                    async with session.begin():
                        if session.bind is not None and session.bind.dialect.name == "postgresql":
                            await session.execute(text("SET LOCAL lock_timeout = '1s'"))
                        db_user = await upsert_user(
                            tg_id=user.id,
                            username=user.username,
                            first_name=user.first_name,
                            last_name=user.last_name,
                            language_code=user.language_code,
                            is_bot=user.is_bot,
                            session=session,
                            only_if_exists=True,
                        )
                        if db_user and self._touch_due(db_user):
                            db_user = await self._touch_user(user.id, session)
            return db_user
        except Exception as error:
            logger.exception(
                "[UserMiddleware] Ошибка SQL профиля: tg_id={}, type={}, sql_elapsed_ms={:.1f}",
                user.id,
                type(error).__name__,
                (perf_counter() - started) * 1000,
            )
            return None

    def _decode_user(self, value: Any) -> dict | None:
        """Восстанавливает даты подтверждённого профиля из кеша."""
        if not isinstance(value, dict):
            return None
        result = dict(value)
        try:
            for name in ("created_at", "updated_at", "legal_accepted_at"):
                if isinstance(result.get(name), str):
                    result[name] = datetime.fromisoformat(result[name])
        except ValueError:
            return None
        return result

    def _touch_due(self, user: dict) -> bool:
        """Проверяет необходимость обновить время активности."""
        updated_at = user.get("updated_at")
        if not isinstance(updated_at, datetime):
            return True
        updated_at = updated_at.replace(tzinfo=UTC) if updated_at.tzinfo is None else updated_at.astimezone(UTC)
        return updated_at <= datetime.now(UTC) - timedelta(seconds=self._debounce)

    async def _touch_user(self, tg_id: int, session: AsyncSession) -> dict | None:
        now = datetime.now(UTC).replace(tzinfo=None)
        res = await session.execute(
            update(DbUser)
            .where(
                DbUser.tg_id == tg_id,
                or_(DbUser.updated_at.is_(None), DbUser.updated_at <= now - timedelta(seconds=self._debounce)),
            )
            .values(updated_at=now)
            .returning(*DbUser.__table__.c)
        )
        row = res.mappings().one_or_none()
        if row is None:
            res = await session.execute(select(*DbUser.__table__.c).where(DbUser.tg_id == tg_id))
            row = res.mappings().one_or_none()
        return dict(row) if row is not None else None

    def _fingerprint(self, user: User) -> str:
        return "|".join([
            str(user.id),
            user.username or "",
            user.first_name or "",
            user.last_name or "",
            user.language_code or "",
            "1" if user.is_bot else "0",
        ])
