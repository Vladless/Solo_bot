import asyncio

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, InlineQuery, Message, TelegramObject, User

from audit import (
    _telegram_access_payload,
    ensure_telegram_context,
    log_telegram_access,
    record_audit_event_to_redis,
    record_telegram_access_event,
    record_telegram_access_event_background,
    set_telegram_actor,
)


try:
    from settings.cache_config import AUDIT_REDIS_BUFFER_ENABLED
except ImportError:
    AUDIT_REDIS_BUFFER_ENABLED = False

from logger import logger
from core.executor import spawn


class UserInfo(TypedDict):
    user_id: int | None
    username: str | None
    action: str | None


def _log_activity_sync(user_info: UserInfo) -> None:
    """Синхронный вывод в лог, чтобы не блокировать event loop в create_task."""
    logger.info(
        f"Активность пользователя │ "
        f"ID: {str(user_info['user_id']).ljust(10)} │ "
        f"Имя: {user_info['username'] or '—':<15} │ "
        f"Действие: {user_info['action'] or '—'}"
    )


class LoggingMiddleware(BaseMiddleware):
    """Middleware для логирования действий пользователя. Лог пишется в фоне.
    Аудит: при включённом Redis-буфере пишем только в Redis (в БД — раз в сутки через drain);
    при выключенном буфере или сбое Redis — пишем в БД."""

    def __init__(self, sessionmaker=None) -> None:
        super().__init__()
        self._sessionmaker = sessionmaker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        audit_context = ensure_telegram_context(data, event)
        user_info = self._extract_user_info(event)

        if user_info["user_id"]:
            spawn(asyncio.to_thread(_log_activity_sync, user_info))

        try:
            result = await handler(event, data)
            db_user = data.get("user")
            actor = data.get("actor")
            if actor is not None:
                set_telegram_actor(
                    audit_context,
                    identity_id=getattr(actor, "identity_id", None),
                    tg_id=getattr(actor, "telegram_chat_id", None),
                )
            if isinstance(db_user, dict):
                set_telegram_actor(
                    audit_context,
                    identity_id=db_user.get("identity_id"),
                    tg_id=db_user.get("tg_id"),
                )
            payload = _telegram_access_payload(audit_context, event, result="success")
            if AUDIT_REDIS_BUFFER_ENABLED:
                if self._sessionmaker is not None:
                    spawn(record_telegram_access_event_background(self._sessionmaker, **payload))
                else:
                    spawn(record_audit_event_to_redis(**payload))
            else:
                if self._sessionmaker is not None:
                    spawn(record_telegram_access_event_background(self._sessionmaker, **payload))
                else:
                    session = data.get("session")
                    if session is not None:
                        await record_telegram_access_event(
                            session,
                            audit_context,
                            event,
                            result="success",
                        )
            spawn(
                asyncio.to_thread(
                    log_telegram_access,
                    event,
                    audit_context=audit_context,
                    result="success",
                )
            )
            return result
        except Exception as exc:
            reason = type(exc).__name__
            payload = _telegram_access_payload(audit_context, event, result="fail", reason=reason)
            if AUDIT_REDIS_BUFFER_ENABLED:
                if self._sessionmaker is not None:
                    spawn(record_telegram_access_event_background(self._sessionmaker, **payload))
                else:
                    spawn(record_audit_event_to_redis(**payload))
            else:
                if self._sessionmaker is not None:
                    spawn(record_telegram_access_event_background(self._sessionmaker, **payload))
                else:
                    session = data.get("session")
                    if session is not None:
                        await record_telegram_access_event(
                            session,
                            audit_context,
                            event,
                            result="fail",
                            reason=reason,
                        )
            spawn(
                asyncio.to_thread(
                    log_telegram_access,
                    event,
                    audit_context=audit_context,
                    result="fail",
                    reason=reason,
                )
            )
            raise

    def _extract_user_info(self, event: TelegramObject) -> UserInfo:
        """Извлекает информацию о пользователе из различных типов событий."""
        result: UserInfo = {"user_id": None, "username": None, "action": None}

        if hasattr(event, "from_user") and isinstance(event.from_user, User):
            result["user_id"] = event.from_user.id
            result["username"] = event.from_user.username

            if isinstance(event, Message):
                result["action"] = f"Сообщение: {event.text}"
            elif isinstance(event, CallbackQuery):
                result["action"] = f"Обратный вызов: {event.data}"
            elif isinstance(event, InlineQuery):
                result["action"] = f"Inline запрос: {event.query}"

        return result
