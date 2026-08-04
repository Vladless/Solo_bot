from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.settings_cache import settings_cache

from ..defaults import DEFAULT_WEB_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


WEB_CONFIG: dict[str, Any] = DEFAULT_WEB_CONFIG.copy()
WEB_SETTING_KEY = "WEB_CONFIG"
register_runtime_config(WEB_SETTING_KEY, WEB_CONFIG)


async def load_web_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == WEB_SETTING_KEY)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        web_config = DEFAULT_WEB_CONFIG.copy()
        setting = Setting(
            key=WEB_SETTING_KEY,
            value=web_config,
            description="Конфигурация веб-сайта",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        web_config = DEFAULT_WEB_CONFIG.copy()
        web_config.update(stored)
        setting.value = web_config

    WEB_CONFIG.clear()
    WEB_CONFIG.update(web_config)
    await session.flush()


async def update_web_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    stmt = select(Setting).where(Setting.key == WEB_SETTING_KEY)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key=WEB_SETTING_KEY,
            value=new_values,
            description="Конфигурация веб-сайта",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.commit()

    web_config = DEFAULT_WEB_CONFIG.copy()
    web_config.update(new_values)

    WEB_CONFIG.clear()
    WEB_CONFIG.update(web_config)
    settings_cache.update(WEB_SETTING_KEY, web_config)
    await publish_runtime_config(WEB_SETTING_KEY, web_config)


def get_site_url() -> str:
    """Возвращает SITE_URL из WEB_CONFIG, или из config.py как fallback."""
    url = str(WEB_CONFIG.get("SITE_URL") or "").strip()
    if url:
        return url.rstrip("/")
    from settings.config import SITE_URL

    return SITE_URL.rstrip("/") if SITE_URL else ""


def is_web_enabled() -> bool:
    return bool(WEB_CONFIG.get("WEB_ENABLED", False))


def is_email_binding_enabled() -> bool:
    return bool(WEB_CONFIG.get("EMAIL_BINDING_ENABLED", False))


def is_web_open_in_browser() -> bool:
    return bool(WEB_CONFIG.get("WEB_OPEN_IN_BROWSER", False))


def get_web_node_status_interval_min() -> int:
    try:
        return max(1, int(WEB_CONFIG.get("WEB_NODE_STATUS_INTERVAL_MIN") or 1))
    except (TypeError, ValueError):
        return 1
