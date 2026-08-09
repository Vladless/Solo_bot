from __future__ import annotations

import time

from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from logger import logger


async def _flush_cache_purges(session) -> None:
    try:
        from database.cache_purge import flush_purges

        await flush_purges(session)
    except Exception as exc:
        logger.warning("[Cache] Отложенный сброс не выполнен: {}", exc)


def _is_bot_blocked_error(exc: BaseException) -> bool:
    """Проверяет, что исключение связано с блокировкой бота пользователем (в т.ч. обёрнутое)."""
    if isinstance(exc, TelegramForbiddenError):
        return True
    msg = str(exc).lower()
    if "blocked by the user" in msg or "bot was blocked" in msg:
        return True
    for link in (getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
        if link is not None and _is_bot_blocked_error(link):
            return True
    return False


try:
    from settings.config import LOG_SESSION_DURATION
except ImportError:
    LOG_SESSION_DURATION = False


async def release_session_early(session: Any) -> bool:
    if hasattr(session, "release_early"):
        return await session.release_early()
    return False


def wrap_session(session: AsyncSession, maker) -> _SessionProxy:
    """Оборачивает сессию в прокси с release_early (для фоновых задач вроде periodic_notifications)."""
    return _SessionProxy(session, maker, {})


class _PendingCall:
    """Вызов к отпущенной сессии: у неё синхронных методов больше нет, всё идёт короткой сессией.

    Если результат не дождались (`session.add(...)` без await), операция до БД не доходит.
    Питон сообщает об этом только предупреждением в stderr — здесь оно превращается в строку лога
    с именем метода, иначе запись теряется молча.
    """

    __slots__ = ("_coro", "_method", "_awaited")

    def __init__(self, coro, method: str) -> None:
        self._coro = coro
        self._method = method
        self._awaited = False

    def __await__(self):
        self._awaited = True
        return self._coro.__await__()

    def __del__(self) -> None:
        if self._awaited:
            return
        try:
            self._coro.close()
            logger.error(
                "Сессия отпущена: результат session.{}() не дождались — операция до БД не дошла. "
                "Нужен `await` на вызове",
                self._method,
            )
        except Exception:
            pass


class _SessionProxy:
    __slots__ = ("_session", "_maker", "_released", "_data")

    def __init__(self, session: AsyncSession, maker, data: dict) -> None:
        self._session = session
        self._maker = maker
        self._released = False
        self._data = data

    async def release_early(self) -> bool:
        if self._released:
            return False
        self._released = True
        try:
            await self._session.commit()
            await _flush_cache_purges(self._session)
        except Exception:
            await self._session.rollback()
        try:
            await self._session.close()
        except Exception:
            pass
        self._session = None
        self._data["_session_released_early"] = True
        return True

    async def _with_short_session(self, method: str, *args, **kwargs):
        import asyncio

        async with self._maker() as s:
            try:
                result = getattr(s, method)(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                await s.commit()
                return result
            except Exception:
                await s.rollback()
                raise

    def __getattr__(self, name: str):
        if name in ("_session", "_maker", "_released", "_data", "release_early", "_with_short_session"):
            raise AttributeError(name)
        if self._released:

            def _short(*a, **k):
                return _PendingCall(self._with_short_session(name, *a, **k), name)

            return _short
        return getattr(self._session, name)


class SessionMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker) -> None:
        self.sessionmaker = sessionmaker

    async def _rollback(self, session: AsyncSession, context: str) -> None:
        try:
            await session.rollback()
        except Exception as rollback_err:
            logger.warning(
                "Session rollback failed during %s — %s: %s",
                context,
                type(rollback_err).__name__,
                rollback_err,
                exc_info=True,
            )

    async def __call__(self, handler, event, data):
        if data.get("session"):
            return await handler(event, data)

        handler_name = getattr(handler, "__qualname__", getattr(handler, "__name__", str(handler)))
        event_type = type(event).__name__
        t0 = time.perf_counter() if LOG_SESSION_DURATION else None

        async with self.sessionmaker() as session:
            try:
                await session.rollback()
            except Exception:
                pass
            proxy = _SessionProxy(session, self.sessionmaker, data)
            data["session"] = proxy
            committed = False
            try:
                result = await handler(event, data)
                if data.get("_session_released_early"):
                    committed = True
                    return result
                try:
                    await session.commit()
                    committed = True
                    await _flush_cache_purges(session)
                    return result
                except Exception as commit_err:
                    logger.warning(
                        "Session commit failed, rolling back — handler=%s, event=%s, error=%s: %s",
                        handler_name,
                        event_type,
                        type(commit_err).__name__,
                        commit_err,
                        exc_info=True,
                    )
                    await self._rollback(session, "commit failure")
                    return result
            except Exception as e:
                if _is_bot_blocked_error(e):
                    logger.debug(
                        "Session rollback: пользователь заблокировал бота — handler={}, event={}",
                        handler_name,
                        event_type,
                    )
                else:
                    logger.warning(
                        "Session rollback: ошибка при обработке — handler={}, event={}, error={}: {}",
                        handler_name,
                        event_type,
                        type(e).__name__,
                        e,
                        exc_info=True,
                    )
                await self._rollback(session, "handler failure")
                raise
            finally:
                if not committed and not data.get("_session_released_early"):
                    try:
                        await session.rollback()
                    except Exception:
                        pass
                if t0 is not None:
                    duration_ms = int((time.perf_counter() - t0) * 1000)
                    logger.debug(
                        "[Session] %s %s handler=%s duration_ms=%d",
                        event_type,
                        getattr(event, "update_id", ""),
                        handler_name,
                        duration_ms,
                    )
