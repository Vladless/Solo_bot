from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database import bulk_add_notifications, bulk_delete_notifications
from logger import logger


def create_bulk_updates() -> dict[str, Any]:
    """Отметки об уведомлениях за цикл: деньги и сроки ключей пишутся сразу, до похода в панель."""
    return {
        "notifications_to_add": [],
        "notifications_to_delete": [],
    }


async def execute_bulk_updates(session: AsyncSession, bulk_updates: dict[str, Any]) -> None:
    try:
        to_add = bulk_updates.get("notifications_to_add") or []
        if to_add:
            await bulk_add_notifications(session, to_add, commit=False)

        to_delete = bulk_updates.get("notifications_to_delete") or []
        if to_delete:
            await bulk_delete_notifications(session, to_delete, commit=False)

        if to_add or to_delete:
            logger.info(f"Bulk: {len(to_add)} добавлений, {len(to_delete)} удалений уведомлений")

        await session.commit()

    except Exception as error:
        logger.error(f"Ошибка в bulk-обновлениях: {error}")
        try:
            await session.rollback()
        except Exception:
            pass
        raise
