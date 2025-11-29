from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting

from ..defaults import DEFAULT_MODES_CONFIG


MODES_CONFIG: dict[str, bool] = DEFAULT_MODES_CONFIG.copy()


async def load_modes_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "MODES_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        modes_config = DEFAULT_MODES_CONFIG.copy()
        setting = Setting(
            key="MODES_CONFIG",
            value=modes_config,
            description="Конфигурация режимов работы бота",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        modes_config = DEFAULT_MODES_CONFIG.copy()
        modes_config.update(stored)
        setting.value = modes_config

    MODES_CONFIG.clear()
    MODES_CONFIG.update(modes_config)
    await session.flush()


async def update_modes_config(session: AsyncSession, new_values: dict[str, bool]) -> None:
    stmt = select(Setting).where(Setting.key == "MODES_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="MODES_CONFIG",
            value=new_values,
            description="Конфигурация режимов работы бота",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.commit()

    modes_config = DEFAULT_MODES_CONFIG.copy()
    modes_config.update(new_values)

    MODES_CONFIG.clear()
    MODES_CONFIG.update(modes_config)
