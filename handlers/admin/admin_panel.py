from aiogram import Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from config import ADMIN_ID, DATABASE_URL
import asyncpg
from datetime import datetime
from bot import bot

router = Router()

class UserEditorState(StatesGroup):
    waiting_for_tg_id = State()
    displaying_user_info = State()

@router.message(Command('admin'))
async def handle_admin_command(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.reply("У вас нет доступа к этой команде.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Статистика пользователей", callback_data="user_stats")],
        [InlineKeyboardButton(text="Редактор пользователей", callback_data="user_editor")]
    ])
    await message.reply("Панель администратора", reply_markup=keyboard)

@router.callback_query(lambda c: c.data == "user_stats")
async def user_stats_menu(callback_query: CallbackQuery):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM connections")
        total_keys = await conn.fetchval("SELECT COUNT(*) FROM keys")
        total_referrals = await conn.fetchval("SELECT COUNT(*) FROM referrals")

        active_keys = await conn.fetchval("SELECT COUNT(*) FROM keys WHERE expiry_time > $1", int(datetime.utcnow().timestamp() * 1000))
        expired_keys = total_keys - active_keys

        stats_message = (
            f"🔹 <b>Общая статистика пользователей:</b>\n"
            f"• Всего пользователей: <b>{total_users}</b>\n"
            f"• Всего ключей: <b>{total_keys}</b>\n"
            f"• Всего рефералов: <b>{total_referrals}</b>\n"
            f"• Активные ключи: <b>{active_keys}</b>\n"
            f"• Истекшие ключи: <b>{expired_keys}</b>"
        )

        back_button = InlineKeyboardButton(text="Назад", callback_data="back_to_admin_menu")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [back_button]
        ])

        await callback_query.message.edit_text(stats_message, reply_markup=keyboard, parse_mode="HTML")
    finally:
        await conn.close()

    await callback_query.answer()

@router.callback_query(lambda c: c.data == "user_editor")
async def user_editor_menu(callback_query: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Поиск по имени ключа", callback_data="search_by_key_name")],
        [InlineKeyboardButton(text="Поиск по tg_id", callback_data="search_by_tg_id")],
        [InlineKeyboardButton(text="Назад", callback_data="back_to_admin_menu")]  # Back button
    ])
    await callback_query.message.edit_text("Выберите метод поиска:", reply_markup=keyboard)

@router.callback_query(lambda c: c.data == "back_to_admin_menu")
async def back_to_admin_menu(callback_query: CallbackQuery):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Статистика пользователей", callback_data="user_stats")],
        [InlineKeyboardButton(text="Редактор пользователей", callback_data="user_editor")]
    ])
    await callback_query.message.edit_text("Панель администратора", reply_markup=keyboard)

async def handle_error(tg_id, callback_query, message):
    await bot.edit_message_text(message, chat_id=tg_id, message_id=callback_query.message.message_id)
