from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot import version
from filters.admin import IsAdminFilter
from keyboards.admin.panel_kb import build_panel_kb, AdminPanelCallback, build_management_kb

router = Router()


@router.callback_query(AdminPanelCallback.filter(F.action == "admin"), IsAdminFilter())
async def handle_admin_callback_query(callback_query: CallbackQuery, state: FSMContext):
    text = f"🤖 Панель администратора\n📌 Версия бота: {version}"

    await state.clear()
    await callback_query.message.edit_text(text=text, reply_markup=build_panel_kb())


@router.callback_query(F.data == "admin", IsAdminFilter())
async def handle_admin_callback_query(callback_query: CallbackQuery, state: FSMContext):
    await handle_admin_message(callback_query.message, state)


@router.message(Command("admin"), IsAdminFilter())
async def handle_admin_message(message: types.Message, state: FSMContext):
    text = f"🤖 Панель администратора\n📌 Версия бота: {version}"

    await state.clear()
    await message.answer(text=text, reply_markup=build_panel_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "management"), IsAdminFilter())
async def handle_management(callback_query: CallbackQuery):
    await callback_query.message.edit_text(
        text="🤖 Управление ботом",
        reply_markup=build_management_kb(),
    )
