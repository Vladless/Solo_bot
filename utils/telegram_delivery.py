import asyncio
import re

from datetime import UTC, datetime

from logger import logger


def unreachable_chat_reason(error: Exception) -> str | None:
    """Различает постоянную недоступность чата и прочие ошибки."""
    description = str(error).lower()
    if "blocked by" in description or "bot was blocked" in description:
        return "blocked"
    if "user is deactivated" in description or "deactivated user" in description:
        return "deactivated"
    if "can't initiate conversation" in description or "cannot initiate conversation" in description:
        return "not_started"
    if "chat not found" in description:
        return "chat_not_found"
    return None


def log_telegram_delivery_error(error: Exception, tg_id: int, user_id: int | None, reason: str) -> None:
    """Записывает причину отказа без адресов API и токенов."""
    detail = re.sub(r"https?://\S+", "<url>", str(error))
    detail = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b", "<token>", detail)
    logger.warning(
        "[Notify] chat={} user_id={} error={} reason={} detail={}",
        tg_id,
        user_id,
        type(error).__name__,
        reason,
        detail,
    )


def blocked_observation_times(stats: dict, field: str = "blocked_observed_at") -> dict[int, datetime]:
    """Восстанавливает время попыток доставки из статистики рассылки."""
    result = {}
    observations = stats.get(field)
    if not isinstance(observations, dict):
        return result
    for chat_id, value in observations.items():
        try:
            observed_at = datetime.fromisoformat(value)
            if observed_at.tzinfo is not None:
                result[int(chat_id)] = observed_at.astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            continue
    return result


async def _publish_telegram_availability(
    tg_id: int, observed_at: datetime, *, is_blocked: bool, user_id: int | None = None
) -> None:
    """Публикует датированное наблюдение доступности чата."""
    if (
        not isinstance(tg_id, int)
        or isinstance(tg_id, bool)
        or tg_id <= 0
        or not isinstance(observed_at, datetime)
        or observed_at.tzinfo is None
    ):
        return
    if user_id is not None and (not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0):
        return
    try:
        from sqlalchemy import text

        from core.redis_cache import cache_rpush
        from database import async_session_maker
        from database.bans import apply_telegram_chat_observations
        from settings.cache_config import BLOCKED_EVENTS_REDIS_KEY

        payload = {
            "tg_id": tg_id,
            "action": "block" if is_blocked else "unblock",
            "observed_at": observed_at.timestamp(),
        }
        if user_id is not None:
            payload["user_id"] = int(user_id)
        try:
            queued = await cache_rpush(BLOCKED_EVENTS_REDIS_KEY, payload)
        except Exception as error:
            logger.warning("[Notify] Очередь статусов недоступна: chat={}, type={}", tg_id, type(error).__name__)
            queued = 0
        if queued:
            return
        async with asyncio.timeout(2):
            async with async_session_maker() as session:
                async with session.begin():
                    if session.bind is not None and session.bind.dialect.name == "postgresql":
                        await session.execute(text("SET LOCAL lock_timeout = '1s'"))
                    await apply_telegram_chat_observations(session, [(user_id, tg_id, is_blocked, observed_at)])
    except Exception as error:
        logger.warning("[Notify] Наблюдение доставки не сохранено: chat={}, type={}", tg_id, type(error).__name__)


async def publish_telegram_reachable(tg_id: int, observed_at: datetime) -> None:
    """Публикует подтверждение успешной доставки."""
    await _publish_telegram_availability(tg_id, observed_at, is_blocked=False)


async def publish_telegram_unreachable(tg_id: int, observed_at: datetime, user_id: int | None = None) -> None:
    """Публикует постоянный отказ доставки для проверенного адреса."""
    await _publish_telegram_availability(tg_id, observed_at, is_blocked=True, user_id=user_id)
