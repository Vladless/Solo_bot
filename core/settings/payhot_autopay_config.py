from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.settings import get_setting, set_setting
from database.settings_cache import settings_cache

from . import payhot_config
from .autopay_locations import AUTOPAY_MANAGEMENT_LOCATIONS
from .payments_config import PAYMENTS_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


PAYHOT_AUTOPAY_SETTING_KEY = "PAYHOT_AUTOPAY_CONFIG"
PAYHOT_AUTOPAY_SETTINGS_INVALID = "Некорректные настройки автоплатежей PayHOT. Проверьте значения полей."
DEFAULT_PAYHOT_AUTOPAY_CONFIG: dict[str, Any] = {
    "DAYS_BEFORE_EXPIRY": 3,
    "MAX_RETRY_ATTEMPTS": 3,
    "RETRY_INTERVAL_HOURS": 24,
    "MIN_PAYMENT_AMOUNT": 0.01,
    "MANAGE_BUTTON_LOCATION": "both",
}
PAYHOT_AUTOPAY_SETTING_LIMITS: dict[str, tuple[int | float, int | float]] = {
    "DAYS_BEFORE_EXPIRY": (0, 3650),
    "MAX_RETRY_ATTEMPTS": (1, 1000),
    "RETRY_INTERVAL_HOURS": (1, 8760),
    "MIN_PAYMENT_AMOUNT": (0.01, 1000000),
}
PAYHOT_AUTOPAY_CONFIG: dict[str, Any] = DEFAULT_PAYHOT_AUTOPAY_CONFIG.copy()
register_runtime_config(PAYHOT_AUTOPAY_SETTING_KEY, PAYHOT_AUTOPAY_CONFIG)


def available_payment_methods() -> list[str]:
    """Возвращает включённые способы рекуррентной оплаты PayHOT."""
    if (
        not payhot_config.PAYHOT_API_KEY
        or payhot_config.PAYHOT_API_KEY.startswith("phk_test_")
        or not payhot_config.PAYHOT_WEBHOOK_SECRET
    ):
        return []
    return [method for method, key in (("card", "PAYHOT_CARDS"), ("sbp", "PAYHOT_SBP")) if PAYMENTS_CONFIG.get(key)]


def is_payhot_autopay_enabled() -> bool:
    """Проверяет кассу и доступность привязки PayHOT."""
    return bool(PAYMENTS_CONFIG.get("PAYHOT_AUTOPAY", False) and available_payment_methods())


async def is_payhot_autopay_enabled_for_session(session: AsyncSession, *, payment_method: str | None = None) -> bool:
    """Читает актуальные переключатели автоплатежей из базы."""
    config = await get_setting(session, "PAYMENTS_CONFIG", {})
    if not (
        payhot_config.PAYHOT_API_KEY
        and not payhot_config.PAYHOT_API_KEY.startswith("phk_test_")
        and payhot_config.PAYHOT_WEBHOOK_SECRET
        and isinstance(config, dict)
        and config.get("PAYHOT_AUTOPAY", False)
    ):
        return False
    return any(
        config.get(key, False)
        for method, key in (("card", "PAYHOT_CARDS"), ("sbp", "PAYHOT_SBP"))
        if payment_method is None or payment_method == method
    )


def validate_payhot_autopay_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Проверяет параметры автопродления и повторных списаний."""
    if not isinstance(values, dict) or set(values).difference(DEFAULT_PAYHOT_AUTOPAY_CONFIG):
        raise ValueError(PAYHOT_AUTOPAY_SETTINGS_INVALID)
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key == "MANAGE_BUTTON_LOCATION":
            if not isinstance(value, str) or value.strip() not in AUTOPAY_MANAGEMENT_LOCATIONS:
                raise ValueError(PAYHOT_AUTOPAY_SETTINGS_INVALID)
            result[key] = value.strip()
            continue
        if key == "MIN_PAYMENT_AMOUNT":
            if isinstance(value, bool) or not isinstance(value, int | float | str):
                raise ValueError(PAYHOT_AUTOPAY_SETTINGS_INVALID)
            try:
                amount = Decimal(str(value))
                minimum, maximum = (Decimal(str(limit)) for limit in PAYHOT_AUTOPAY_SETTING_LIMITS[key])
                if (
                    not amount.is_finite()
                    or not minimum <= amount <= maximum
                    or amount != amount.quantize(Decimal("0.01"))
                ):
                    raise ValueError(PAYHOT_AUTOPAY_SETTINGS_INVALID)
            except (InvalidOperation, ValueError, OverflowError) as exc:
                raise ValueError(PAYHOT_AUTOPAY_SETTINGS_INVALID) from exc
            result[key] = float(amount)
            continue
        minimum, maximum = PAYHOT_AUTOPAY_SETTING_LIMITS[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not minimum <= value <= maximum
            or value != int(value)
        ):
            raise ValueError(PAYHOT_AUTOPAY_SETTINGS_INVALID)
        result[key] = int(value)
    return result


def _normalized_config(values: Any) -> dict[str, Any]:
    """Восстанавливает безопасные значения повреждённых настроек."""
    config = DEFAULT_PAYHOT_AUTOPAY_CONFIG.copy()
    if isinstance(values, dict):
        for key in DEFAULT_PAYHOT_AUTOPAY_CONFIG:
            if key not in values:
                continue
            try:
                config.update(validate_payhot_autopay_settings({key: values[key]}))
            except ValueError:
                continue
    return config


async def load_payhot_autopay_config(session: AsyncSession) -> None:
    """Загружает параметры автоплатежей при запуске."""
    config = _normalized_config(await get_setting(session, PAYHOT_AUTOPAY_SETTING_KEY))
    await set_setting(session, PAYHOT_AUTOPAY_SETTING_KEY, config, "Настройки автоплатежей PayHOT")
    PAYHOT_AUTOPAY_CONFIG.clear()
    PAYHOT_AUTOPAY_CONFIG.update(config)


async def update_payhot_autopay_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    """Сохраняет параметры и синхронизирует работающие процессы."""
    values = validate_payhot_autopay_settings(new_values)
    config = _normalized_config(await get_setting(session, PAYHOT_AUTOPAY_SETTING_KEY, {}))
    config.update(values)
    await set_setting(session, PAYHOT_AUTOPAY_SETTING_KEY, config, "Настройки автоплатежей PayHOT")
    await session.commit()
    PAYHOT_AUTOPAY_CONFIG.clear()
    PAYHOT_AUTOPAY_CONFIG.update(config)
    settings_cache.update(PAYHOT_AUTOPAY_SETTING_KEY, config)
    await publish_runtime_config(PAYHOT_AUTOPAY_SETTING_KEY, config)
