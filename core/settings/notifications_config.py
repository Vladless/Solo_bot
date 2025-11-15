from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from ..defaults import DEFAULT_NOTIFICATIONS_CONFIG


NOTIFICATIONS_CONFIG: dict[str, Any] = DEFAULT_NOTIFICATIONS_CONFIG.copy()


async def load_notifications_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "NOTIFICATIONS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        notifications_config = DEFAULT_NOTIFICATIONS_CONFIG.copy()
        setting = Setting(
            key="NOTIFICATIONS_CONFIG",
            value=notifications_config,
            description="Конфигурация уведомлений",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        notifications_config = DEFAULT_NOTIFICATIONS_CONFIG.copy()
        notifications_config.update(stored)
        setting.value = notifications_config

    NOTIFICATIONS_CONFIG.clear()
    NOTIFICATIONS_CONFIG.update(notifications_config)
    await session.flush()


async def update_notifications_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    stmt = select(Setting).where(Setting.key == "NOTIFICATIONS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="NOTIFICATIONS_CONFIG",
            value=new_values,
            description="Конфигурация уведомлений",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.flush()

    notifications_config = DEFAULT_NOTIFICATIONS_CONFIG.copy()
    notifications_config.update(new_values)

    NOTIFICATIONS_CONFIG.clear()
    NOTIFICATIONS_CONFIG.update(notifications_config)
