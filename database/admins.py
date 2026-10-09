from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from filters.permissions import (
    ALL_ACTION_PERMISSIONS,
    GRANULAR_ACTIONS_CONFIGURED,
    STORED_PERMISSIONS,
    normalize_permissions,
)

from .models import Admin


async def get_admin_by_tg_id(session: AsyncSession, tg_id: int) -> Admin | None:
    """Читает актуальные права администратора по Telegram ID."""
    statement = select(Admin).where(Admin.tg_id == tg_id).execution_options(populate_existing=True)
    return (await session.execute(statement)).scalar_one_or_none()


async def list_moderators_for_permissions(session: AsyncSession, protected_tg_ids: Sequence[int] = ()) -> list[Admin]:
    """Читает модераторов без главных администраторов."""
    statement = select(Admin).where(func.lower(func.trim(Admin.role)) == "moderator")
    if protected_tg_ids:
        statement = statement.where(Admin.tg_id.not_in(protected_tg_ids))
    statement = statement.order_by(Admin.tg_id).execution_options(populate_existing=True)
    return list((await session.execute(statement)).scalars().all())


async def save_moderator_action_permissions(
    session: AsyncSession,
    tg_id: int,
    selected: Sequence[str],
    protected_tg_ids: Sequence[int] = (),
) -> Admin | None:
    """Сохраняет действия модератора, не меняя права разделов."""
    if tg_id in protected_tg_ids:
        return None
    statement = select(Admin).where(Admin.tg_id == tg_id).with_for_update().execution_options(populate_existing=True)
    admin = (await session.execute(statement)).scalar_one_or_none()
    if admin is None or (admin.role or "").strip().lower() != "moderator":
        return None
    current = set(normalize_permissions(admin.permissions)).difference(ALL_ACTION_PERMISSIONS)
    current.update(selected)
    current.add(GRANULAR_ACTIONS_CONFIGURED)
    admin.permissions = [permission for permission in STORED_PERMISSIONS if permission in current]
    await session.commit()
    return admin
