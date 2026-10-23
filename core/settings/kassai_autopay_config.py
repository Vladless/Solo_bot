from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.settings import get_setting, set_setting
from database.settings_cache import settings_cache

from .autopay_locations import AUTOPAY_MANAGEMENT_LOCATIONS
from .payments_config import PAYMENTS_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


KASSAI_AUTOPAY_SETTING_KEY = "KASSAI_AUTOPAY_CONFIG"
KASSAI_AUTOPAY_SETTINGS_INVALID = "Некорректные настройки автоплатежей KassaAI. Проверьте значения полей."
DEFAULT_KASSAI_AUTOPAY_CONFIG: dict[str, Any] = {
    "DAYS_BEFORE_EXPIRY": 3,
    "MAX_RETRY_ATTEMPTS": 3,
    "RETRY_INTERVAL_HOURS": 24,
    "MIN_PAYMENT_AMOUNT": 1.0,
    "MANAGE_BUTTON_LOCATION": "both",
    "API_TIMEOUT": 30,
}
KASSAI_AUTOPAY_SETTING_LIMITS: dict[str, tuple[int | float, int | float]] = {
    "DAYS_BEFORE_EXPIRY": (0, 3650),
    "MAX_RETRY_ATTEMPTS": (1, 1000),
    "RETRY_INTERVAL_HOURS": (1, 8760),
    "MIN_PAYMENT_AMOUNT": (1, 1000000),
    "API_TIMEOUT": (1, 300),
}
KASSAI_AUTOPAY_CONFIG: dict[str, Any] = DEFAULT_KASSAI_AUTOPAY_CONFIG.copy()
register_runtime_config(KASSAI_AUTOPAY_SETTING_KEY, KASSAI_AUTOPAY_CONFIG)


def is_kassai_autopay_enabled() -> bool:
    """Проверяет общий переключатель автоплатежей KassaAI."""
    return bool(PAYMENTS_CONFIG.get("KASSAI_AUTOPAY", False))


async def is_kassai_autopay_enabled_for_session(session: AsyncSession) -> bool:
    """Читает актуальный переключатель автоплатежей из базы."""
    config = await get_setting(session, "PAYMENTS_CONFIG", {})
    return bool(isinstance(config, dict) and config.get("KASSAI_AUTOPAY", False))


def validate_kassai_autopay_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Проверяет параметры автопродления и повторных списаний."""
    if not isinstance(values, dict) or set(values).difference(DEFAULT_KASSAI_AUTOPAY_CONFIG):
        raise ValueError(KASSAI_AUTOPAY_SETTINGS_INVALID)
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key == "MANAGE_BUTTON_LOCATION":
            if not isinstance(value, str) or value.strip() not in AUTOPAY_MANAGEMENT_LOCATIONS:
                raise ValueError(KASSAI_AUTOPAY_SETTINGS_INVALID)
            result[key] = value.strip()
            continue
        if key == "MIN_PAYMENT_AMOUNT":
            if isinstance(value, bool) or not isinstance(value, int | float | str):
                raise ValueError(KASSAI_AUTOPAY_SETTINGS_INVALID)
            try:
                amount = Decimal(str(value))
                minimum, maximum = (Decimal(str(limit)) for limit in KASSAI_AUTOPAY_SETTING_LIMITS[key])
                if (
                    not amount.is_finite()
                    or not minimum <= amount <= maximum
                    or amount != amount.quantize(Decimal("0.01"))
                ):
                    raise ValueError(KASSAI_AUTOPAY_SETTINGS_INVALID)
            except (InvalidOperation, ValueError, OverflowError) as exc:
                raise ValueError(KASSAI_AUTOPAY_SETTINGS_INVALID) from exc
            result[key] = float(amount)
            continue
        minimum, maximum = KASSAI_AUTOPAY_SETTING_LIMITS[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not minimum <= value <= maximum
            or value != int(value)
        ):
            raise ValueError(KASSAI_AUTOPAY_SETTINGS_INVALID)
        result[key] = int(value)
    return result


def _normalized_config(values: Any) -> dict[str, Any]:
    """Восстанавливает безопасные значения повреждённых настроек."""
    config = DEFAULT_KASSAI_AUTOPAY_CONFIG.copy()
    if isinstance(values, dict):
        for key in DEFAULT_KASSAI_AUTOPAY_CONFIG:
            if key not in values:
                continue
            try:
                config.update(validate_kassai_autopay_settings({key: values[key]}))
            except ValueError:
                continue
    return config


async def load_kassai_autopay_config(session: AsyncSession) -> None:
    """Загружает параметры автоплатежей при запуске."""
    config = _normalized_config(await get_setting(session, KASSAI_AUTOPAY_SETTING_KEY))
    await set_setting(session, KASSAI_AUTOPAY_SETTING_KEY, config, "Настройки автоплатежей KassaAI")
    KASSAI_AUTOPAY_CONFIG.clear()
    KASSAI_AUTOPAY_CONFIG.update(config)


async def update_kassai_autopay_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    """Сохраняет параметры и синхронизирует работающие процессы."""
    values = validate_kassai_autopay_settings(new_values)
    config = _normalized_config(await get_setting(session, KASSAI_AUTOPAY_SETTING_KEY, {}))
    config.update(values)
    await set_setting(session, KASSAI_AUTOPAY_SETTING_KEY, config, "Настройки автоплатежей KassaAI")
    await session.commit()
    KASSAI_AUTOPAY_CONFIG.clear()
    KASSAI_AUTOPAY_CONFIG.update(config)
    settings_cache.update(KASSAI_AUTOPAY_SETTING_KEY, config)
    await publish_runtime_config(KASSAI_AUTOPAY_SETTING_KEY, config)
