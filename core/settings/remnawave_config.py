from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.settings_cache import settings_cache

from ..defaults import DEFAULT_REMNAWAVE_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


REMNAWAVE_CONFIG: dict[str, Any] = DEFAULT_REMNAWAVE_CONFIG.copy()
REMNAWAVE_SETTING_KEY = "REMNAWAVE_CONFIG"
register_runtime_config(REMNAWAVE_SETTING_KEY, REMNAWAVE_CONFIG)


async def load_remnawave_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == REMNAWAVE_SETTING_KEY)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        merged = DEFAULT_REMNAWAVE_CONFIG.copy()
        setting = Setting(
            key=REMNAWAVE_SETTING_KEY,
            value=merged,
            description="Конфигурация интеграции с Remnawave (мониторинг + ротация хостов)",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        merged = DEFAULT_REMNAWAVE_CONFIG.copy()
        merged.update(stored)
        setting.value = merged

    REMNAWAVE_CONFIG.clear()
    REMNAWAVE_CONFIG.update(merged)
    await session.flush()


async def update_remnawave_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    stmt = select(Setting).where(Setting.key == REMNAWAVE_SETTING_KEY)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    merged = DEFAULT_REMNAWAVE_CONFIG.copy()
    merged.update(new_values)

    if setting is None:
        setting = Setting(
            key=REMNAWAVE_SETTING_KEY,
            value=merged,
            description="Конфигурация интеграции с Remnawave (мониторинг + ротация хостов)",
        )
        session.add(setting)
    else:
        setting.value = merged

    await session.commit()

    REMNAWAVE_CONFIG.clear()
    REMNAWAVE_CONFIG.update(merged)
    settings_cache.update(REMNAWAVE_SETTING_KEY, merged)
    await publish_runtime_config(REMNAWAVE_SETTING_KEY, merged)


def is_node_health_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("NODE_HEALTH_ENABLED", False))


def is_host_rotation_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("HOST_ROTATION_ENABLED", False))


def is_host_auto_disable_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("HOST_AUTO_DISABLE_ON_NODE_DOWN", False))


def get_host_auto_disabled() -> set[str]:
    raw = REMNAWAVE_CONFIG.get("HOST_AUTO_DISABLED") or []
    if not isinstance(raw, list):
        return set()
    return {str(uuid) for uuid in raw if uuid}


def get_host_rotation_allowed() -> set[str]:
    raw = REMNAWAVE_CONFIG.get("HOST_ROTATION_ALLOWED") or []
    if not isinstance(raw, list):
        return set()
    return {str(uuid) for uuid in raw if uuid}


def get_node_health_allowed() -> set[str]:
    raw = REMNAWAVE_CONFIG.get("NODE_HEALTH_ALLOWED") or []
    if not isinstance(raw, list):
        return set()
    return {str(uuid) for uuid in raw if uuid}


def is_load_monitor_enabled() -> bool:
    return bool(REMNAWAVE_CONFIG.get("LOAD_MONITOR_ENABLED", False))


def get_load_monitor_groups() -> list[dict[str, Any]]:
    raw = REMNAWAVE_CONFIG.get("LOAD_MONITOR_GROUPS") or []
    if not isinstance(raw, list):
        return []
    return [dict(group) for group in raw if isinstance(group, dict) and group.get("id")]
