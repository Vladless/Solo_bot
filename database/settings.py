from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Setting


async def get_setting(session: AsyncSession, key: str, default: Any = None) -> Any:
    stmt = select(Setting).where(Setting.key == key)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()
    if setting is None or setting.value is None:
        return default
    return setting.value


async def set_setting(
    session: AsyncSession,
    key: str,
    value: Any,
    description: str | None = None,
) -> Setting:
    stmt = select(Setting).where(Setting.key == key)
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(key=key, value=value, description=description)
        session.add(setting)
    else:
        setting.value = value
        if description is not None:
            setting.description = description

    await session.flush()
    return setting
