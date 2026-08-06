import re

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key
from filters.admin import HasPermission
from filters.permissions import PERM_MANAGEMENT
from logger import logger

from ..panel.headers import menu_text, quote
from ..panel.keyboard import build_admin_back_kb
from . import router
from .keyboard import AdminPanelCallback


class AdminManagementStates(StatesGroup):
    waiting_for_new_domain = State()


@router.callback_query(AdminPanelCallback.filter(F.action == "change_domain"), HasPermission(PERM_MANAGEMENT))
async def request_new_domain(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(AdminManagementStates.waiting_for_new_domain)
    await callback_query.message.edit_text(
        text=menu_text("Домен", "🌐 Введите новый домен (без https://):", quote("Пример: solobotdomen.ru")),
    )


@router.message(AdminManagementStates.waiting_for_new_domain, HasPermission(PERM_MANAGEMENT))
async def process_new_domain(message: Message, state: FSMContext, session: AsyncSession):
    new_domain = message.text.strip()

    if not re.fullmatch(r"[a-zA-Z0-9.-]+", new_domain) or " " in new_domain:
        logger.warning("[DomainChange] Некорректный домен")
        await message.answer(
            menu_text(
                "Домен",
                "❌ Домен без http:// и без пробелов.",
                markup=build_admin_back_kb("admin"),
            ),
            reply_markup=build_admin_back_kb("admin"),
        )
        return

    new_domain_url = f"https://{new_domain}"

    try:
        stmt = (
            update(Key)
            .values(
                key=func.regexp_replace(Key.key, r"^https://[^/]+", new_domain_url),
                remnawave_link=func.regexp_replace(Key.remnawave_link, r"^https://[^/]+", new_domain_url),
            )
            .where(
                (Key.key.startswith("https://") & ~Key.key.startswith(new_domain_url))
                | (Key.remnawave_link.startswith("https://") & ~Key.remnawave_link.startswith(new_domain_url))
            )
        )
        await session.execute(stmt)
        logger.info("[DomainChange] Запрос на обновление домена выполнен успешно.")
    except Exception as e:
        logger.error(f"[DomainChange] Ошибка при выполнении запроса: {e}")
        await message.answer(
            menu_text("Домен", f"❌ Ошибка при обновлении домена: {e}", markup=build_admin_back_kb("admin")),
            reply_markup=build_admin_back_kb("admin"),
        )
        return

    try:
        sample = await session.execute(select(Key.key, Key.remnawave_link).limit(1))
        example = sample.fetchone()
        logger.info(f"[DomainChange] Пример обновленной записи: {example}")
    except Exception as e:
        logger.error(f"[DomainChange] Ошибка при выборке обновленной записи: {e}")

    await message.answer(
        menu_text("Домен", f"✅ Домен теперь <code>{new_domain}</code>.", markup=build_admin_back_kb("admin")),
        reply_markup=build_admin_back_kb("admin"),
    )
    await state.clear()
