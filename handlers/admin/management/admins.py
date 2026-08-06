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

from ..panel.headers import menu_text, quote, section
from . import router
from .keyboard import (
    AdminPanelCallback,
    build_admin_back_kb_to_admins,
    build_admin_permissions_kb,
    build_admins_kb,
    build_new_admin_role_kb,
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
    await callback.message.edit_text(
        menu_text("Админы", "Кто имеет доступ к админке.", markup=build_admins_kb(admins)),
        reply_markup=build_admins_kb(admins),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "add_admin"), HasPermission(PERM_ADMINS))
async def prompt_new_admin(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        menu_text("Админы", "Введите <code>tg_id</code> нового админа:", markup=build_admin_back_kb_to_admins()),
        reply_markup=build_admin_back_kb_to_admins(),
    )
    await state.set_state(AdminState.waiting_for_tg_id)


@router.message(AdminState.waiting_for_tg_id, HasPermission(PERM_ADMINS))
async def save_new_admin(message: Message, session: AsyncSession, state: FSMContext):
    try:
        tg_id = int(message.text.strip())
    except ValueError:
        await message.answer(menu_text("Админы", "❌ Неверный формат. Введите числовой <code>tg_id</code>."))
        return

    await state.clear()

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    if result.scalar_one_or_none():
        await message.answer(
            menu_text("Админы", "⚠️ Такой админ уже существует.", markup=build_admin_back_kb_to_admins()),
            reply_markup=build_admin_back_kb_to_admins(),
        )
        return

    await message.answer(
        menu_text("Админы", f"Выберите роль для <code>{tg_id}</code>:", markup=build_new_admin_role_kb(tg_id)),
        reply_markup=build_new_admin_role_kb(tg_id),
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("add_role|")), HasPermission(PERM_ADMINS))
async def create_admin_with_role(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    try:
        _, tg_id_str, role = callback_data.action.split("|")
        tg_id = int(tg_id_str)
        if role not in ("moderator", "designer"):
            raise ValueError
    except Exception:
        await callback.message.edit_text(
            menu_text("Админы", "❌ Неверный формат.", markup=build_admin_back_kb_to_admins()),
            reply_markup=build_admin_back_kb_to_admins(),
        )
        return

    existing = (await session.execute(select(Admin).where(Admin.tg_id == tg_id))).scalar_one_or_none()
    if existing:
        await callback.message.edit_text(
            menu_text("Админы", "⚠️ Такой админ уже существует.", markup=build_admin_back_kb_to_admins()),
            reply_markup=build_admin_back_kb_to_admins(),
        )
        return

    session.add(Admin(tg_id=tg_id, role=role, description="Добавлен вручную", permissions=[]))
    invalidate_admin_cache(tg_id)
    await callback.message.edit_text(
        menu_text(
            "Админы",
            f"✅ Админ <code>{tg_id}</code> добавлен с ролью <b>{role}</b>.",
            markup=build_admin_back_kb_to_admins(),
        ),
        reply_markup=build_admin_back_kb_to_admins(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("admin_menu|")), HasPermission(PERM_ADMINS))
async def open_admin_menu(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Admin.role).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    role = admin or "moderator"

    await callback.message.edit_text(
        menu_text("Админы", f"<code>{tg_id}</code>", markup=build_single_admin_menu(tg_id, role)),
        reply_markup=build_single_admin_menu(tg_id, role),
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("generate_token|")), HasPermission(PERM_ADMINS))
async def generate_token(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    if not admin:
        await callback.message.edit_text(menu_text("Админы", "Админ не найден"))
        return

    token = Admin.generate_token()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    admin.token = token_hash

    msg = await callback.message.edit_text(
        menu_text(
            "Админы",
            f"Новый токен для <code>{tg_id}</code>.",
            section("🎟 Токен", token),
            quote("Сообщение исчезнет через 5 минут."),
            markup=build_token_result_kb(token),
        ),
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
        menu_text("Админы", f"Новая роль для <code>{tg_id}</code>.", markup=build_role_selection_kb(tg_id)),
        reply_markup=build_role_selection_kb(tg_id),
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("set_role|")), HasPermission(PERM_ADMINS))
async def set_admin_role(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    try:
        _, tg_id_str, role = callback_data.action.split("|")
        tg_id = int(tg_id_str)
        if role not in ("superadmin", "moderator", "designer"):
            raise ValueError
    except Exception:
        await callback.message.edit_text(menu_text("Админы", "❌ Неверный формат."))
        return

    if tg_id == callback.from_user.id:
        await callback.message.edit_text(
            menu_text("Админы", "❌ Свою роль изменить нельзя.", markup=build_single_admin_menu(tg_id)),
            reply_markup=build_single_admin_menu(tg_id),
        )
        return

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    if not admin:
        await callback.message.edit_text(menu_text("Админы", "Админ не найден"))
        return

    admin.role = role
    invalidate_admin_cache(tg_id)

    await callback.message.edit_text(
        menu_text(
            "Админы",
            f"✅ Роль админа <code>{tg_id}</code> изменена на <b>{role}</b>.",
            markup=build_single_admin_menu(tg_id, role),
        ),
        reply_markup=build_single_admin_menu(tg_id, role),
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("edit_perms|")), HasPermission(PERM_ADMINS))
async def edit_admin_permissions(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    admin = (await session.execute(select(Admin).where(Admin.tg_id == tg_id))).scalar_one_or_none()
    if not admin:
        await callback.message.edit_text(
            menu_text("Админы", "Админ не найден", markup=build_admin_back_kb_to_admins()),
            reply_markup=build_admin_back_kb_to_admins(),
        )
        return

    current = set(normalize_permissions(admin.permissions))
    await callback.message.edit_text(
        menu_text(
            "Права админа",
            f"<code>{tg_id}</code>",
            quote(
                "Нажмите на право, чтобы включить или выключить его.",
                "У роли superadmin права всегда полные.",
            ),
            markup=build_admin_permissions_kb(tg_id, current),
        ),
        reply_markup=build_admin_permissions_kb(tg_id, current),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action.startswith("toggle_perm|")), HasPermission(PERM_ADMINS), flags={"popup": True}
)
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
        menu_text("Админы", f"🗑 Админ <code>{tg_id}</code> удалён.", markup=build_admin_back_kb_to_admins()),
        reply_markup=build_admin_back_kb_to_admins(),
    )
