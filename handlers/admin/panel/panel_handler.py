from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Admin
from filters.admin import IsAdminFilter
from logger import logger
from utils.versioning import get_version

from .keyboard import AdminPanelCallback, build_panel_kb


router = Router()


@router.callback_query(AdminPanelCallback.filter(F.action == "admin"), IsAdminFilter())
async def handle_admin_callback_query(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    text = f"🤖 Панель администратора\n\nВерсия бота:\n<blockquote>{get_version()}</blockquote>"

    await state.clear()

    result = await session.execute(select(Admin.role).where(Admin.tg_id == callback_query.from_user.id))
    role = result.scalar_one_or_none() or "admin"

    markup = await build_panel_kb(admin_role=role)

    if callback_query.message.text:
        try:
            await callback_query.message.edit_text(
                text=text,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                logger.warning("🔄 Попытка редактировать сообщение без изменений — пропущено.")
            else:
                raise
    else:
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        await callback_query.message.answer(
            text=text,
            reply_markup=markup,
            disable_web_page_preview=True,
        )


@router.callback_query(F.data == "admin", IsAdminFilter())
async def handle_admin_callback_query_simple(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await handle_admin_callback_query(callback_query, state, session)


@router.message(Command("admin"), IsAdminFilter())
async def handle_admin_message(message: Message, state: FSMContext, session: AsyncSession):
    text = f"🤖 Панель администратора\n\nВерсия бота:\n<blockquote>{get_version()}</blockquote>"

    await state.clear()

    result = await session.execute(select(Admin.role).where(Admin.tg_id == message.from_user.id))
    role = result.scalar_one_or_none() or "admin"

    await message.answer(
        text=text,
        reply_markup=await build_panel_kb(admin_role=role),
        disable_web_page_preview=True,
    )
