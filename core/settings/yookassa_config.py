from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from database.settings import get_setting, set_setting
from database.settings_cache import settings_cache
from settings import config, texts

from .runtime_sync import publish_runtime_config, register_runtime_config


YOOKASSA_SETTING_KEY = "YOOKASSA_CONFIG"
DEFAULT_YOOKASSA_CONFIG = {
    "MARKUP_ENABLED": bool(getattr(config, "YOOKASSA_MARKUP_ENABLED", False)),
    "MARKUP_PERCENT": str(getattr(config, "YOOKASSA_MARKUP_PERCENT", 0)),
}
YOOKASSA_CONFIG = DEFAULT_YOOKASSA_CONFIG.copy()
register_runtime_config(YOOKASSA_SETTING_KEY, YOOKASSA_CONFIG)


def normalize_yookassa_config(values: dict | None) -> dict:
    """Проверяет процент наценки и состояние настройки."""
    result = {**DEFAULT_YOOKASSA_CONFIG, **(values or {})}
    try:
        percent = Decimal(str(result["MARKUP_PERCENT"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(texts.YOOKASSA_MARKUP_INVALID) from exc
    if not percent.is_finite() or not 0 <= percent <= 100:
        raise ValueError(texts.YOOKASSA_MARKUP_INVALID)
    return {"MARKUP_ENABLED": result["MARKUP_ENABLED"] is True, "MARKUP_PERCENT": str(percent)}


async def load_yookassa_config(session: AsyncSession) -> None:
    """Загружает настройки наценки кассы."""
    stored = await get_setting(session, YOOKASSA_SETTING_KEY, {})
    values = normalize_yookassa_config(stored)
    await set_setting(session, YOOKASSA_SETTING_KEY, values, "Настройки YooKassa")
    YOOKASSA_CONFIG.clear()
    YOOKASSA_CONFIG.update(values)


async def update_yookassa_config(session: AsyncSession, values: dict) -> None:
    """Сохраняет настройки наценки и обновляет процессы."""
    updated = normalize_yookassa_config({**YOOKASSA_CONFIG, **values})
    await set_setting(session, YOOKASSA_SETTING_KEY, updated, "Настройки YooKassa")
    await session.commit()
    YOOKASSA_CONFIG.clear()
    YOOKASSA_CONFIG.update(updated)
    settings_cache.update(YOOKASSA_SETTING_KEY, updated)
    await publish_runtime_config(YOOKASSA_SETTING_KEY, updated)
