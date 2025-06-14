from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
import hashlib

from database.models import Key, Admin
from filters.admin import IsAdminFilter
from logger import logger
from middlewares import maintenance

from ..panel.keyboard import build_admin_back_kb
from .keyboard import AdminPanelCallback, build_management_kb, build_admins_kb, build_single_admin_menu, build_role_selection_kb, build_admin_back_kb_to_admins, build_token_result_kb
from asyncio import sleep


router = Router()


class AdminManagementStates(StatesGroup):
    waiting_for_new_domain = State()


class AdminState(StatesGroup):
    waiting_for_tg_id = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "management"), IsAdminFilter()
)
async def handle_management(callback_query: CallbackQuery, session: AsyncSession):
    tg_id = callback_query.from_user.id

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()

    if not admin:
        await callback_query.message.edit_text("❌ Вы не зарегистрированы как администратор.")
        return

    await callback_query.message.edit_text(
        text="🤖 Управление ботом",
        reply_markup=build_management_kb(admin.role),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "change_domain"), IsAdminFilter()
)
async def request_new_domain(callback_query: CallbackQuery, state: FSMContext):
    """Запрашивает у администратора новый домен."""
    await state.set_state(AdminManagementStates.waiting_for_new_domain)
    await callback_query.message.edit_text(
        text="🌐 Введите новый домен (без https://):\nПример: solobotdomen.ru",
    )


@router.message(AdminManagementStates.waiting_for_new_domain)
async def process_new_domain(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Обновляет домен в таблице keys."""
    new_domain = message.text.strip()
    logger.info(
        f"[DomainChange] Новый домен, введённый администратором: '{new_domain}'"
    )

    if not new_domain or " " in new_domain or not new_domain.replace(".", "").isalnum():
        logger.warning("[DomainChange] Некорректный домен")
        await message.answer(
            "🚫 Некорректный домен! Введите домен без http:// и без пробелов.",
            reply_markup=build_admin_back_kb("admin"),
        )
        return

    new_domain_url = f"https://{new_domain}"
    logger.info(f"[DomainChange] Новый домен с протоколом: '{new_domain_url}'")

    try:
        stmt = (
            update(Key)
            .where(~Key.key.startswith(new_domain_url))
            .values(key=func.regexp_replace(Key.key, r"^https://[^/]+", new_domain_url))
        )
        await session.execute(stmt)
        await session.commit()
        logger.info("[DomainChange] Запрос на обновление домена выполнен успешно.")
    except Exception as e:
        logger.error(f"[DomainChange] Ошибка при выполнении запроса: {e}")
        await message.answer(
            f"❌ Ошибка при обновлении домена: {e}",
            reply_markup=build_admin_back_kb("admin"),
        )
        return

    try:
        sample = await session.execute(select(Key.key).limit(1))
        example = sample.scalar()
        logger.info(f"[DomainChange] Пример обновленной записи: {example}")
    except Exception as e:
        logger.error(f"[DomainChange] Ошибка при выборке обновленной записи: {e}")

    await message.answer(
        f"✅ Домен успешно изменен на {new_domain}!",
        reply_markup=build_admin_back_kb("admin"),
    )
    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action == "toggle_maintenance"))
async def toggle_maintenance_mode(callback: CallbackQuery, session: AsyncSession):
    tg_id = callback.from_user.id

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()

    if not admin:
        await callback.answer("❌ Админ не найден.", show_alert=True)
        return

    maintenance.maintenance_mode = not maintenance.maintenance_mode
    new_status = "включён" if maintenance.maintenance_mode else "выключен"
    await callback.answer(f"🛠️ Режим обслуживания {new_status}.", show_alert=True)

    await callback.message.edit_reply_markup(
        reply_markup=build_management_kb(admin.role)
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "admins"))
async def show_admins(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(select(Admin.tg_id, Admin.role))
    admins = result.all()
    await callback.message.edit_text(
        "👑 <b>Список админов</b>",
        reply_markup=build_admins_kb(admins)
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "add_admin"))
async def prompt_new_admin(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Введите <code>tg_id</code> нового админа:")
    await state.set_state(AdminState.waiting_for_tg_id)


@router.message(AdminState.waiting_for_tg_id)
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
        session.add(Admin(
            tg_id=tg_id,
            role="admin",
            description="Добавлен вручную"
        ))
        await session.commit()
        await message.answer(
            f"✅ Админ <code>{tg_id}</code> добавлен.",
            reply_markup=build_admin_back_kb_to_admins()
        )

    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("admin_menu|")))
async def open_admin_menu(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Admin.role).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    role = admin or "moderator"

    await callback.message.edit_text(
        f"👤 <b>Управление админом</b> <code>{tg_id}</code>",
        reply_markup=build_single_admin_menu(tg_id, role)
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("generate_token|")))
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
    await session.commit()

    msg = await callback.message.edit_text(
        f"🎟 <b>Новый токен для</b> <code>{tg_id}</code>:\n\n"
        f"<code>{token}</code>\n\n"
        f"⚠️ Это сообщение исчезнет через 5 минут.",
        reply_markup=build_token_result_kb(token)
    )

    await sleep(300)
    try:
        await msg.delete()
    except Exception:
        pass


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("edit_role|")))
async def edit_admin_role(callback: CallbackQuery, callback_data: AdminPanelCallback):
    tg_id = int(callback_data.action.split("|")[1])
    await callback.message.edit_text(
        f"✏ <b>Выберите новую роль для</b> <code>{tg_id}</code>:",
        reply_markup=build_role_selection_kb(tg_id)
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("set_role|")))
async def set_admin_role(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    try:
        _, tg_id_str, role = callback_data.action.split("|")
        tg_id = int(tg_id_str)
        if role not in ("superadmin", "moderator"):
            raise ValueError
    except Exception:
        await callback.message.edit_text("❌ Неверный формат.")
        return

    result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
    admin = result.scalar_one_or_none()
    if not admin:
        await callback.message.edit_text("❌ Админ не найден.")
        return

    admin.role = role
    await session.commit()

    await callback.message.edit_text(
        f"✅ Роль админа <code>{tg_id}</code> изменена на <b>{role}</b>.",
        reply_markup=build_single_admin_menu(tg_id)
    )


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("delete_admin|")))
async def delete_admin(callback: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    tg_id = int(callback_data.action.split("|")[1])

    await session.execute(delete(Admin).where(Admin.tg_id == tg_id))
    await session.commit()

    await callback.message.edit_text(
        f"🗑 Админ <code>{tg_id}</code> удалён.",
        reply_markup=build_admin_back_kb_to_admins()
    )
