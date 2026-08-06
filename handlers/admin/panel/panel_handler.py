from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.executor import run_io
from database.models import Admin
from filters.admin import IsAdminFilter, get_admin_context
from logger import logger
from utils.versioning import get_version

from .keyboard import AdminPanelCallback, build_panel_kb
from .summary import render_panel_text


router = Router()


async def _send_panel(message: Message, version_text, markup, *, edit: bool) -> None:
    """Панель — текстовое сообщение: разделы админки правят его через edit_text,
    поэтому картинкой её делать нельзя, иначе переходы ломаются."""
    text = render_panel_text(version_text)

    if edit and message.text:
        try:
            await message.edit_text(text=text, reply_markup=markup, disable_web_page_preview=True)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                logger.warning("🔄 Попытка редактировать сообщение без изменений — пропущено.")
                return
            raise

    if edit:
        try:
            await message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

    await message.answer(text=text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(AdminPanelCallback.filter(F.action == "admin"), IsAdminFilter())
async def handle_admin_callback_query(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()

    result = await session.execute(select(Admin.role).where(Admin.tg_id == callback_query.from_user.id))
    role = result.scalar_one_or_none() or "admin"

    _, is_super, perms = await get_admin_context(callback_query.from_user.id)
    version_text = await run_io(get_version, is_super) if is_super else None

    markup = await build_panel_kb(admin_role=role, permissions=perms)
    await _send_panel(callback_query.message, version_text, markup, edit=True)


@router.callback_query(F.data == "admin", IsAdminFilter())
async def handle_admin_callback_query_simple(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await handle_admin_callback_query(callback_query, state, session)


@router.message(Command("admin"), IsAdminFilter())
async def handle_admin_message(message: Message, state: FSMContext, session: AsyncSession):
    await state.clear()

    result = await session.execute(select(Admin.role).where(Admin.tg_id == message.from_user.id))
    role = result.scalar_one_or_none() or "admin"

    _, is_super, perms = await get_admin_context(message.from_user.id)
    version_text = await run_io(get_version, is_super) if is_super else None

    markup = await build_panel_kb(admin_role=role, permissions=perms)
    await _send_panel(message, version_text, markup, edit=False)
