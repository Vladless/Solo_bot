from datetime import datetime, timezone

from core.defaults import DEFAULT_NOTIFICATIONS_CONFIG
from core.redis_cache import cache_get, cache_key, cache_set
from core.settings.modes_config import MODES_CONFIG
from core.settings.notifications_config import NOTIFICATIONS_CONFIG
from logger import logger


_INITIAL_TRUST = 100.0
_TRUST_KEY_PREFIX = "hwid_delete_trust"
_MAX_TTL_SEC = 60 * 60 * 24 * 60


def is_cooldown_enabled() -> bool:
    return bool(MODES_CONFIG.get("HWID_DELETE_COOLDOWN_ENABLED", False))


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cfg(key: str, max_value: float | None = None) -> float:
    fallback = float(DEFAULT_NOTIFICATIONS_CONFIG[key])
    value = _to_float(NOTIFICATIONS_CONFIG.get(key, fallback), fallback)
    if value < 0:
        value = fallback
    if max_value is not None and value > max_value:
        value = max_value
    return value


def _penalty() -> float:
    return _cfg("HWID_DELETE_PENALTY", _INITIAL_TRUST)


def _recovery_per_day() -> float:
    return _cfg("HWID_DAILY_RECOVERY")


def _min_trust() -> float:
    return _cfg("HWID_MIN_TRUST_TO_DELETE", _INITIAL_TRUST)


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _key(client_id: str) -> str:
    return cache_key(_TRUST_KEY_PREFIX, client_id)


def _current_trust(score: float, updated_at: float) -> float:
    days_passed = max(0.0, (_now() - updated_at) / 86400)
    recovered = days_passed * _recovery_per_day()
    return min(score + recovered, _INITIAL_TRUST)


def format_wait_time(days: float) -> str:
    if days <= 0:
        return "0 мин."
    full_days = int(days)
    hours = int((days - full_days) * 24)
    if full_days > 0 and hours > 0:
        return f"{full_days} д. {hours} ч."
    if full_days > 0:
        return f"{full_days} д."
    if hours > 0:
        return f"{hours} ч."
    minutes = int((days - full_days) * 24 * 60)
    return f"{minutes} мин." if minutes > 0 else "1 мин."


async def check_delete_allowed(client_id: str | None) -> tuple[bool, float]:
    if not is_cooldown_enabled() or not client_id:
        return True, 0.0

    try:
        data = await cache_get(_key(client_id))
    except Exception as e:
        logger.error(f"[hwid_cooldown] Ошибка чтения trust для {client_id}: {e}")
        return True, 0.0

    if not isinstance(data, dict):
        return True, 0.0

    updated_at = _to_float(data.get("updated_at"), 0.0)
    if updated_at <= 0:
        return True, 0.0

    score = _to_float(data.get("score"), _INITIAL_TRUST)
    current = _current_trust(score, updated_at)
    min_trust = _min_trust()
    if current >= min_trust:
        return True, 0.0

    recovery = _recovery_per_day()
    if recovery <= 0:
        return False, float(_MAX_TTL_SEC) / 86400
    wait_days = (min_trust - current) / recovery
    return False, wait_days


async def register_deletion(client_id: str | None) -> None:
    if not is_cooldown_enabled() or not client_id:
        return

    try:
        data = await cache_get(_key(client_id))
        updated_at = _to_float(data.get("updated_at"), 0.0) if isinstance(data, dict) else 0.0
        if updated_at > 0:
            current = _current_trust(_to_float(data.get("score"), _INITIAL_TRUST), updated_at)
        else:
            current = _INITIAL_TRUST

        new_score = max(current - _penalty(), 0.0)

        recovery = _recovery_per_day()
        if recovery > 0:
            ttl = int(((_INITIAL_TRUST - new_score) / recovery) * 86400) + 1
        else:
            ttl = _MAX_TTL_SEC
        ttl = max(1, min(ttl, _MAX_TTL_SEC))

        await cache_set(_key(client_id), {"score": new_score, "updated_at": _now()}, ttl)
        logger.info(f"[hwid_cooldown] Штраф применён для {client_id}: trust={new_score:.0f}")
    except Exception as e:
        logger.error(f"[hwid_cooldown] Ошибка обновления trust для {client_id}: {e}")
