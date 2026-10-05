from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.settings_cache import settings_cache

from ..defaults import DEFAULT_MODES_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


MODES_CONFIG: dict[str, bool] = DEFAULT_MODES_CONFIG.copy()
register_runtime_config("MODES_CONFIG", MODES_CONFIG)


def resolve_protect_content() -> bool:
    return bool(MODES_CONFIG.get("PROTECT_CONTENT_ENABLED", False))


def apply_protect_content_to_bot() -> None:
    import sys

    bot_module = sys.modules.get("bot")
    if bot_module is None:
        return
    bot = getattr(bot_module, "bot", None)
    if bot is None or getattr(bot, "default", None) is None:
        return
    try:
        bot.default.protect_content = resolve_protect_content()
    except Exception:
        pass


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
    apply_protect_content_to_bot()
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
    apply_protect_content_to_bot()
    settings_cache.update("MODES_CONFIG", modes_config)
    await publish_runtime_config("MODES_CONFIG", modes_config)
