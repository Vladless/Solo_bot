from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.settings_cache import settings_cache
from settings.config import YOOKASSA_SBP_ONLY

from ..defaults import DEFAULT_PAYMENTS_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


PAYMENTS_CONFIG: dict[str, bool] = DEFAULT_PAYMENTS_CONFIG.copy()
register_runtime_config("PAYMENTS_CONFIG", PAYMENTS_CONFIG)


async def load_payments_config(session: AsyncSession) -> None:
    from .yookassa_autopay_config import migrate_missing_autopay_flag

    stmt = select(Setting).where(Setting.key == "PAYMENTS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        payments_config = DEFAULT_PAYMENTS_CONFIG.copy()
        payments_config.update(await migrate_missing_autopay_flag(session, {}))
        if YOOKASSA_SBP_ONLY and payments_config.get("YOOKASSA"):
            payments_config.update(YOOKASSA=False, YOOKASSA_SBP=True)
        setting = Setting(
            key="PAYMENTS_CONFIG",
            value=payments_config,
            description="Конфигурация платёжных провайдеров",
        )
        session.add(setting)
    else:
        stored = await migrate_missing_autopay_flag(session, setting.value or {})
        if "YOOKASSA_SBP" not in stored and YOOKASSA_SBP_ONLY and stored.get("YOOKASSA"):
            stored = {**stored, "YOOKASSA": False, "YOOKASSA_SBP": True}
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
    settings_cache.update("PAYMENTS_CONFIG", payments_config)
    await publish_runtime_config("PAYMENTS_CONFIG", payments_config)
