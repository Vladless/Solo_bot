from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting

from ..defaults import DEFAULT_PAYMENTS_CONFIG


PAYMENTS_CONFIG: dict[str, bool] = DEFAULT_PAYMENTS_CONFIG.copy()


async def load_payments_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "PAYMENTS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        payments_config = DEFAULT_PAYMENTS_CONFIG.copy()
        setting = Setting(
            key="PAYMENTS_CONFIG",
            value=payments_config,
            description="Конфигурация платёжных провайдеров",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        payments_config = DEFAULT_PAYMENTS_CONFIG.copy()
        payments_config.update(stored)
        setting.value = payments_config

    PAYMENTS_CONFIG.clear()
    PAYMENTS_CONFIG.update(payments_config)
    await session.flush()


async def update_payments_config(session: AsyncSession, new_values: dict[str, bool]) -> None:
    stmt = select(Setting).where(Setting.key == "PAYMENTS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="PAYMENTS_CONFIG",
            value=new_values,
            description="Конфигурация платёжных провайдеров",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.commit()

    payments_config = DEFAULT_PAYMENTS_CONFIG.copy()
    payments_config.update(new_values)

    PAYMENTS_CONFIG.clear()
    PAYMENTS_CONFIG.update(payments_config)
