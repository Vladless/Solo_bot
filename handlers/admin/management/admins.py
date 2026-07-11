import hashlib

from asyncio import sleep

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Admin
from filters.admin import HasPermission, invalidate_admin_cache
from filters.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_LABELS,
    PERM_ADMINS,
    normalize_permissions,
)

from . import router
from .keyboard import (
    AdminPanelCallback,
    build_admin_back_kb_to_admins,
    build_admin_permissions_kb,
    build_admins_kb,
    build_role_selection_kb,
    build_single_admin_menu,
    build_token_result_kb,
)


class AdminState(StatesGroup):
    waiting_for_tg_id = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "admins"), HasPermission(PERM_ADMINS))
async def show_admins(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(select(Admin.tg_id, Admin.role))
    admins = result.all()
    await callback.message.edit_text("👑 <b>Список админов</b>", reply_markup=build_admins_kb(admins))


@router.callback_query(AdminPanelCallback.filter(F.action == "add_admin"), HasPermission(PERM_ADMINS))
async def prompt_new_admin(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введите <code>tg_id</code> нового админа:", reply_markup=build_admin_back_kb_to_admins()
    )
    await state.set_state(AdminState.waiting_for_tg_id)


@router.message(AdminState.waiting_for_tg_id, HasPermission(PERM_ADMINS))
async def save_new_admin(message: Message, session: AsyncSession, state: FSMContext):
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный формат. Введите числовой <code>tg_id</code>.")
        return

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    if result.scalar_one_or_none():
        await message.answer("⚠️ Такой админ уже существует.")
    else:
        session.add(Admin(tg_id=tg_id, role="moderator", description="Добавлен вручную", permissions=[]))
        invalidate_admin_cache(tg_id)
        await message.answer(f"✅ Админ <code>{tg_id}</code> добавлен.", reply_markup=build_admin_back_kb_to_admins())

    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("admin_menu|")), HasPermission(PERM_ADMINS))
async def open_admin_menu(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Admin.role).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    role = admin or "moderator"

    await callback.message.edit_text(
        f"👤 <b>Управление админом</b> <code>{tg_id}</code>", reply_markup=build_single_admin_menu(tg_id, role)
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("generate_token|")), HasPermission(PERM_ADMINS))
async def generate_token(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    if not admin:
        await callback.message.edit_text("❌ Админ не найден.")
        return

    token = Admin.generate_token()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    admin.token = token_hash

    msg = await callback.message.edit_text(
        f"🎟 <b>Новый токен для</b> <code>{tg_id}</code>:\n\n"
        f"<code>{token}</code>\n\n"
        f"⚠️ Это сообщение исчезнет через 5 минут.",
        reply_markup=build_token_result_kb(token),
    )

    await sleep(300)
    try:
        await msg.delete()
    except Exception:
        pass


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("edit_role|")), HasPermission(PERM_ADMINS))
async def edit_admin_role(callback: CallbackQuery, callback_data: AdminPanelCallback):
    tg_id = int(callback_data.action.split("|")[1])
    await callback.message.edit_text(
        f"✏ <b>Выберите новую роль для</b> <code>{tg_id}</code>:", reply_markup=build_role_selection_kb(tg_id)
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("set_role|")), HasPermission(PERM_ADMINS))
async def set_admin_role(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    try:
        _, tg_id_str, role = callback_data.action.split("|")
        tg_id = int(tg_id_str)
        if role not in ("superadmin", "moderator"):
            raise ValueError
    except Exception:
        await callback.message.edit_text("❌ Неверный формат.")
        return

    if tg_id == callback.from_user.id:
        await callback.message.edit_text(
            "🚫 <b>Нельзя изменить свою собственную роль!</b>", reply_markup=build_single_admin_menu(tg_id)
        )
        return

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    if not admin:
        await callback.message.edit_text("❌ Админ не найден.")
        return

    admin.role = role
    invalidate_admin_cache(tg_id)

    await callback.message.edit_text(
        f"✅ Роль админа <code>{tg_id}</code> изменена на <b>{role}</b>.",
        reply_markup=build_single_admin_menu(tg_id, role),
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("edit_perms|")), HasPermission(PERM_ADMINS))
async def edit_admin_permissions(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    admin = (await session.execute(select(Admin).where(Admin.tg_id == tg_id))).scalar_one_or_none()
    if not admin:
        await callback.message.edit_text("❌ Админ не найден.", reply_markup=build_admin_back_kb_to_admins())
        return

    current = set(normalize_permissions(admin.permissions))
    await callback.message.edit_text(
        f"🔐 <b>Права админа</b> <code>{tg_id}</code>\n\nНажмите чтобы переключить. "
        f"Роль <b>superadmin</b> имеет все права автоматически.",
        reply_markup=build_admin_permissions_kb(tg_id, current),
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("toggle_perm|")), HasPermission(PERM_ADMINS), flags={"popup": True})
async def toggle_admin_permission(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    try:
        _, tg_id_str, perm_id = callback_data.action.split("|", 2)
        tg_id = int(tg_id_str)
    except ValueError:
        await callback.answer("Неверный формат", show_alert=True)
        return

    if perm_id not in PERMISSION_LABELS:
        await callback.answer("Неизвестное право", show_alert=True)
        return

    admin = (await session.execute(select(Admin).where(Admin.tg_id == tg_id))).scalar_one_or_none()
    if not admin:
        await callback.answer("Админ не найден", show_alert=True)
        return

    current = set(normalize_permissions(admin.permissions))
    if perm_id in current:
        current.discard(perm_id)
    else:
        current.add(perm_id)
    admin.permissions = [p for p in ALL_PERMISSIONS if p in current]
    invalidate_admin_cache(tg_id)

    await callback.message.edit_reply_markup(reply_markup=build_admin_permissions_kb(tg_id, current))
    await callback.answer()


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("delete_admin|")), HasPermission(PERM_ADMINS))
async def delete_admin(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    await session.execute(delete(Admin).where(Admin.tg_id == tg_id))
    invalidate_admin_cache(tg_id)

    await callback.message.edit_text(
        f"🗑 Админ <code>{tg_id}</code> удалён.", reply_markup=build_admin_back_kb_to_admins()
    )
