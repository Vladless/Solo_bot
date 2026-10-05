from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.settings_cache import settings_cache

from ..defaults import DEFAULT_BUTTONS_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


BUTTONS_CONFIG: dict[str, bool] = DEFAULT_BUTTONS_CONFIG.copy()
BUTTONS_CONFIG.pop("TOGGLE_CLIENT_BUTTON_ENABLE", None)
BUTTONS_CONFIG.setdefault("ANDROID_TV_BUTTON_ENABLE", False)
BUTTONS_CONFIG.setdefault("COUPON_BUTTON_ENABLE", True)
register_runtime_config("BUTTONS_CONFIG", BUTTONS_CONFIG)


async def load_buttons_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "BUTTONS_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        buttons_config = DEFAULT_BUTTONS_CONFIG.copy()
        buttons_config.pop("TOGGLE_CLIENT_BUTTON_ENABLE", None)
        buttons_config.setdefault("ANDROID_TV_BUTTON_ENABLE", False)
        buttons_config.setdefault("COUPON_BUTTON_ENABLE", True)
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
        buttons_config.pop("TOGGLE_CLIENT_BUTTON_ENABLE", None)
        buttons_config.setdefault("ANDROID_TV_BUTTON_ENABLE", False)
        buttons_config.setdefault("COUPON_BUTTON_ENABLE", True)
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

    await session.commit()

    buttons_config = DEFAULT_BUTTONS_CONFIG.copy()
    buttons_config.update(new_values)
    buttons_config.pop("TOGGLE_CLIENT_BUTTON_ENABLE", None)
    buttons_config.setdefault("ANDROID_TV_BUTTON_ENABLE", False)
    buttons_config.setdefault("COUPON_BUTTON_ENABLE", True)

    BUTTONS_CONFIG.clear()
    BUTTONS_CONFIG.update(buttons_config)
    settings_cache.update("BUTTONS_CONFIG", buttons_config)
    await publish_runtime_config("BUTTONS_CONFIG", buttons_config)
