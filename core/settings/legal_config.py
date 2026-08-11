from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.settings_cache import settings_cache

from ..defaults import DEFAULT_LEGAL_CONFIG
from .runtime_sync import publish_runtime_config, register_runtime_config


LEGAL_CONFIG: dict[str, Any] = DEFAULT_LEGAL_CONFIG.copy()
register_runtime_config("LEGAL_CONFIG", LEGAL_CONFIG)

LEGAL_DOC_KEYS: tuple[str, ...] = ("LEGAL_PRIVACY_URL", "LEGAL_TERMS_URL", "LEGAL_OFFER_URL")


def is_legal_enabled() -> bool:
    """Раздел показывается только когда включён и есть хотя бы одна ссылка."""
    if not bool(LEGAL_CONFIG.get("LEGAL_DOCS_ENABLED", False)):
        return False
    return any(str(LEGAL_CONFIG.get(key) or "").strip() for key in LEGAL_DOC_KEYS)


def legal_doc_url(key: str) -> str:
    return str(LEGAL_CONFIG.get(key) or "").strip()


async def load_legal_config(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "LEGAL_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        legal_config = DEFAULT_LEGAL_CONFIG.copy()
        setting = Setting(
            key="LEGAL_CONFIG",
            value=legal_config,
            description="Правовые документы бота",
        )
        session.add(setting)
    else:
        stored = setting.value or {}
        legal_config = DEFAULT_LEGAL_CONFIG.copy()
        legal_config.update(stored)
        setting.value = legal_config

    LEGAL_CONFIG.clear()
    LEGAL_CONFIG.update(legal_config)
    await session.flush()


async def update_legal_config(session: AsyncSession, new_values: dict[str, Any]) -> None:
    stmt = select(Setting).where(Setting.key == "LEGAL_CONFIG")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="LEGAL_CONFIG",
            value=new_values,
            description="Правовые документы бота",
        )
        session.add(setting)
    else:
        setting.value = new_values

    await session.commit()

    legal_config = DEFAULT_LEGAL_CONFIG.copy()
    legal_config.update(new_values)

    LEGAL_CONFIG.clear()
    LEGAL_CONFIG.update(legal_config)
    settings_cache.update("LEGAL_CONFIG", legal_config)
    await publish_runtime_config("LEGAL_CONFIG", legal_config)
