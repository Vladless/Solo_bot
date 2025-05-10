from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from asyncpg import Connection

from filters.admin import IsAdminFilter
from logger import logger
from middlewares import maintenance

from ..panel.keyboard import build_admin_back_kb
from .keyboard import AdminPanelCallback, build_management_kb


router = Router()


class AdminManagementStates(StatesGroup):
    waiting_for_new_domain = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "management"), IsAdminFilter())
async def handle_management(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        text="🤖 Управление ботом",
        reply_markup=build_management_kb(),
    )


@router.callback_query(AdminPanelCallback.filter(F.action == "change_domain"), IsAdminFilter())
async def request_new_domain(callback_query: CallbackQuery, state: FSMContext):
    """Запрашивает у администратора новый домен."""
    await state.set_state(AdminManagementStates.waiting_for_new_domain)
    await callback_query.message.edit_text(
        text="🌐 Введите новый домен (без https://):\nПример: solobotdomen.ru",
    )


@router.message(AdminManagementStates.waiting_for_new_domain)
async def process_new_domain(message: Message, state: FSMContext, session: Connection):
    """Обновляет домен в таблице keys."""
    new_domain = message.text.strip()
    logger.info(f"[DomainChange] Новый домен, введённый администратором: '{new_domain}'")

    if not new_domain or " " in new_domain or not new_domain.replace(".", "").isalnum():
        logger.warning("[DomainChange] Некорректный домен")
        await message.answer(
            "🚫 Некорректный домен! Введите домен без http:// и без пробелов.",
            reply_markup=build_admin_back_kb("admin"),
        )
        return

    new_domain_url = f"https://{new_domain}"
    logger.info(f"[DomainChange] Новый домен с протоколом: '{new_domain_url}'")

    query = """
        UPDATE keys
        SET key = regexp_replace(key, '^https://[^/]+', $1::TEXT)
        WHERE key NOT LIKE $1 || '%'
    """
    try:
        await session.execute(query, new_domain_url)
        logger.info("[DomainChange] Запрос на обновление домена выполнен успешно.")
    except Exception as e:
        logger.error(f"[DomainChange] Ошибка при выполнении запроса: {e}")
        await message.answer(f"❌ Ошибка при обновлении домена: {e}", reply_markup=build_admin_back_kb("admin"))
        return

    try:
        sample = await session.fetchrow("SELECT key FROM keys LIMIT 1")
        logger.info(f"[DomainChange] Пример обновленной записи: {sample}")
    except Exception as e:
        logger.error(f"[DomainChange] Ошибка при выборке обновленной записи: {e}")

    await message.answer(f"✅ Домен успешно изменен на {new_domain}!", reply_markup=build_admin_back_kb("admin"))
    await state.clear()


@router.callback_query(AdminPanelCallback.filter(F.action == "toggle_maintenance"))
async def toggle_maintenance_mode(callback: CallbackQuery):
    maintenance.maintenance_mode = not maintenance.maintenance_mode

    new_status = "включён" if maintenance.maintenance_mode else "выключен"
    await callback.answer(f"🛠️ Режим обслуживания {new_status}.", show_alert=True)

    await callback.message.edit_reply_markup(reply_markup=build_management_kb())
