from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Setting
from database.settings_cache import settings_cache

from ..defaults import DEFAULT_MENU_LAYOUT
from .runtime_sync import publish_runtime_config, register_runtime_config


MENU_LAYOUT: dict[str, list[list[str]]] = {menu: [row.copy() for row in rows] for menu, rows in DEFAULT_MENU_LAYOUT.items()}
register_runtime_config("MENU_LAYOUT", MENU_LAYOUT)


def _normalize(stored: object) -> dict[str, list[list[str]]]:
    """Приводит сохранённую раскладку к виду «меню → ряды → идентификаторы»."""
    layout = {menu: [row.copy() for row in rows] for menu, rows in DEFAULT_MENU_LAYOUT.items()}
    if not isinstance(stored, dict):
        return layout

    for menu, rows in stored.items():
        if menu not in layout or not isinstance(rows, list):
            continue
        clean = [
            [str(button) for button in row if isinstance(button, str) and button]
            for row in rows
            if isinstance(row, list)
        ]
        layout[menu] = [row for row in clean if row]
    return layout


async def load_menu_layout(session: AsyncSession) -> None:
    stmt = select(Setting).where(Setting.key == "MENU_LAYOUT")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="MENU_LAYOUT",
            value=DEFAULT_MENU_LAYOUT,
            description="Порядок кнопок в меню бота",
        )
        session.add(setting)
        layout = _normalize(DEFAULT_MENU_LAYOUT)
    else:
        layout = _normalize(setting.value)

    MENU_LAYOUT.clear()
    MENU_LAYOUT.update(layout)
    await session.flush()


async def update_menu_layout(session: AsyncSession, new_layout: dict[str, list[list[str]]]) -> None:
    layout = _normalize(new_layout)

    stmt = select(Setting).where(Setting.key == "MENU_LAYOUT")
    result = await session.execute(stmt)
    setting = result.scalar_one_or_none()

    if setting is None:
        setting = Setting(
            key="MENU_LAYOUT",
            value=layout,
            description="Порядок кнопок в меню бота",
        )
        session.add(setting)
    else:
        setting.value = layout

    await session.commit()

    MENU_LAYOUT.clear()
    MENU_LAYOUT.update(layout)
    settings_cache.update("MENU_LAYOUT", layout)
    await publish_runtime_config("MENU_LAYOUT", layout)
