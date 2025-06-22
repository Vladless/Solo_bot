from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import version
from filters.admin import IsAdminFilter
from logger import logger

from .keyboard import AdminPanelCallback, build_panel_kb

router = Router()


@router.callback_query(AdminPanelCallback.filter(F.action == "admin"), IsAdminFilter())
async def handle_admin_callback_query(callback_query: CallbackQuery, state: FSMContext):
    text = f"🤖 Панель администратора\n\n📌 Версия бота: {version}"

    await state.clear()

    if callback_query.message.text:
        try:
            await callback_query.message.edit_text(
                text=text,
                reply_markup=build_panel_kb(),
                disable_web_page_preview=True,
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                logger.warning(
                    "🔄 Попытка редактировать сообщение без изменений — пропущено."
                )
            else:
                raise
    else:
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        await callback_query.message.answer(
            text=text,
            reply_markup=build_panel_kb(),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data == "admin", IsAdminFilter())
async def handle_admin_callback_query_simple(callback_query: CallbackQuery, state: FSMContext):
    await handle_admin_callback_query(callback_query, state)


@router.message(Command("admin"), IsAdminFilter())
async def handle_admin_message(message: Message, state: FSMContext):
    text = f"🤖 Панель администратора\n\n📌 Версия бота: {version}"

    await state.clear()
    await message.answer(text=text, reply_markup=build_panel_kb())
