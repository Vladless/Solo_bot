from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from ..defaults import DEFAULT_MONEY_CONFIG


MONEY_CONFIG: dict[str, Any] = DEFAULT_MONEY_CONFIG.copy()


async def load_money_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "MONEY_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        money_config = DEFAULT_MONEY_CONFIG.copy()
        setting = Setting(
            key="MONEY_CONFIG",
            value=money_config,
            description="Конфигурация валютных настроек",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        money_config = DEFAULT_MONEY_CONFIG.copy()
        money_config.update(stored)
        setting.value = money_config

    MONEY_CONFIG.clear()
    MONEY_CONFIG.update(money_config)
    await session.flush()


async def update_money_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    stmt = select(Setting).where(Setting.key == "MONEY_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="MONEY_CONFIG",
            value=new_values,
            description="Конфигурация валютных настроек",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.flush()

    money_config = DEFAULT_MONEY_CONFIG.copy()
    money_config.update(new_values)

    MONEY_CONFIG.clear()
    MONEY_CONFIG.update(money_config)
