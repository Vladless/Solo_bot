from aiogram import F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MANAGEMENT_CONFIG, update_management_config
from database.models import Admin
from filters.admin import HasPermission, get_admin_context
from filters.permissions import PERM_ADMINS, PERM_MANAGEMENT

from . import router
from .keyboard import AdminPanelCallback, build_management_kb


@router.callback_query(
    AdminPanelCallback.filter(F.action == "management"),
    HasPermission(PERM_MANAGEMENT, PERM_ADMINS),
)
async def handle_management(callback_query: CallbackQuery, session: AsyncSession):
    tg_id = callback_query.from_user.id

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()

    if not admin:
        await callback_query.message.edit_text("❌ Вы не зарегистрированы как администратор.")
        return

    _, _, perms = await get_admin_context(tg_id)
    await callback_query.message.edit_text(
        text="🤖 Управление ботом",
        reply_markup=build_management_kb(admin.role, permissions=perms),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "toggle_maintenance"),
    HasPermission(PERM_MANAGEMENT),
    flags={"popup": True},
)
async def toggle_maintenance_mode(callback: CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()

    if not admin:
        await callback.answer("❌ Админ не найден.", show_alert=True)
        return

    current_config = dict(MANAGEMENT_CONFIG)
    current_value = bool(current_config.get("MAINTENANCE_ENABLED", False))
    new_value = not current_value
    current_config["MAINTENANCE_ENABLED"] = new_value

    await update_management_config(session, current_config)

    new_status = "включён" if new_value else "выключен"
    await callback.answer(f"🛠️ Режим обслуживания {new_status}.", show_alert=True)

    _, _, perms = await get_admin_context(tg_id)
    await callback.message.edit_reply_markup(reply_markup=build_management_kb(admin.role, permissions=perms))
