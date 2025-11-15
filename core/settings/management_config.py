from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from ..defaults import DEFAULT_MANAGEMENT_CONFIG


MANAGEGENT_CONFIG: dict[str, Any] = DEFAULT_MANAGEMENT_CONFIG.copy()


async def load_management_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "management")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        management_config = DEFAULT_MANAGEMENT_CONFIG.copy()
        setting = Setting(
            key="management",
            value=management_config,
            description="Конфигурация управления ботом",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        management_config = DEFAULT_MANAGEMENT_CONFIG.copy()
        management_config.update(stored)
        setting.value = management_config

    MANAGEGENT_CONFIG.clear()
    MANAGEGENT_CONFIG.update(management_config)
    await session.flush()


async def update_management_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    stmt = select(Setting).where(Setting.key == "management")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="management",
            value=new_values,
            description="Конфигурация управления ботом",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.flush()
    await session.commit()

    management_config = DEFAULT_MANAGEMENT_CONFIG.copy()
    management_config.update(new_values)

    MANAGEGENT_CONFIG.clear()
    MANAGEGENT_CONFIG.update(management_config)
