from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from ..defaults import DEFAULT_BUTTONS_CONFIG


BUTTONS_CONFIG: dict[str, bool] = DEFAULT_BUTTONS_CONFIG.copy()


async def load_buttons_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "BUTTONS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        buttons_config = DEFAULT_BUTTONS_CONFIG.copy()
        setting = Setting(
            key="BUTTONS_CONFIG",
            value=buttons_config,
            description="Конфигурация кнопок бота",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        buttons_config = DEFAULT_BUTTONS_CONFIG.copy()
        buttons_config.update(stored)
        setting.value = buttons_config

    BUTTONS_CONFIG.clear()
    BUTTONS_CONFIG.update(buttons_config)
    await session.flush()


async def update_buttons_config(session: AsyncSession, new_values: dict[str, bool]) -> None:
    stmt = select(Setting).where(Setting.key == "BUTTONS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="BUTTONS_CONFIG",
            value=new_values,
            description="Конфигурация кнопок бота",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.flush()

    buttons_config = DEFAULT_BUTTONS_CONFIG.copy()
    buttons_config.update(new_values)

    BUTTONS_CONFIG.clear()
    BUTTONS_CONFIG.update(buttons_config)
