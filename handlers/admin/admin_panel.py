import subprocess
from datetime import datetime

import asyncpg
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from backup import backup_database
from bot import bot
from config import DATABASE_URL
from filters.admin import IsAdminFilter
from handlers.admin.admin_commands import send_message_to_all_clients

router = Router()


class UserEditorState(StatesGroup):
    waiting_for_tg_id = State()
    displaying_user_info = State()


@router.message(Command("admin"), IsAdminFilter())
async def handle_admin_command(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика пользователей", callback_data="user_stats"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="👥 Управление пользователями", callback_data="user_editor"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🎟️ Управление купонами", callback_data="coupons_editor"
        )
    )
    builder.row(
        InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="send_to_alls")
    )
    builder.row(
        InlineKeyboardButton(text="💾 Создать резервную копию", callback_data="backups")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Перезагрузить бота", callback_data="restart_bot")
    )
    await bot.send_message(
        message.chat.id, "🤖 Панель администратора", reply_markup=builder.as_markup()
    )


@router.callback_query(F.data == "user_stats", IsAdminFilter())
async def user_stats_menu(callback_query: CallbackQuery):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM connections")
        total_keys = await conn.fetchval("SELECT COUNT(*) FROM keys")
        total_referrals = await conn.fetchval("SELECT COUNT(*) FROM referrals")

        active_keys = await conn.fetchval(
            "SELECT COUNT(*) FROM keys WHERE expiry_time > $1",
            int(datetime.utcnow().timestamp() * 1000),
        )
        expired_keys = total_keys - active_keys

        stats_message = (
            f"📈 <b>Подробная статистика проекта:</b>\n\n"
            f"👤 Зарегистрированных пользователей: <b>{total_users}</b>\n"
            f"🔑 Всего сгенерированных ключей: <b>{total_keys}</b>\n"
            f"🤝 Привлеченных рефералов: <b>{total_referrals}</b>\n"
            f"✅ Действующих ключей: <b>{active_keys}</b>\n"
            f"❌ Просроченных ключей: <b>{expired_keys}</b>"
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="🔙 Вернуться в меню", callback_data="back_to_admin_menu"
            )
        )

        await callback_query.message.edit_text(
            stats_message, reply_markup=builder.as_markup(), parse_mode="HTML"
        )
    finally:
        await conn.close()
    await callback_query.answer()


@router.callback_query(F.data == "send_to_alls", IsAdminFilter())
async def handle_send_to_all(callback_query: CallbackQuery, state: FSMContext):
    await send_message_to_all_clients(callback_query.message, state, from_panel=True)
    await callback_query.answer()


@router.callback_query(F.data == "backups", IsAdminFilter())
async def handle_backup(message: Message):
    await message.answer("💾 Инициализация резервного копирования базы данных...")
    await backup_database()
    await message.answer(
        "✅ Резервная копия успешно создана и отправлена администратору."
    )


@router.callback_query(F.data == "restart_bot", IsAdminFilter())
async def handle_restart(callback_query: CallbackQuery):
    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "bot.service"],
            check=True,
            capture_output=True,
            text=True,
        )
        await callback_query.message.answer("🔄 Бот успешно перезапущен.")
    except subprocess.CalledProcessError as e:
        await callback_query.message.answer(
            f"⚠️ Перезагрузка бота будет выполнена через 30 секунд. Детали: {e.stderr}"
        )


@router.callback_query(F.data == "user_editor", IsAdminFilter())
async def user_editor_menu(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔍 Поиск по названию ключа", callback_data="search_by_key_name"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🆔 Поиск по Telegram ID", callback_data="search_by_tg_id"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Вернуться назад", callback_data="back_to_admin_menu"
        )
    )
    await callback_query.message.edit_text(
        "👇 Выберите способ поиска пользователя:", reply_markup=builder.as_markup()
    )


@router.callback_query(F.data == "back_to_admin_menu", IsAdminFilter())
async def back_to_admin_menu(callback_query: CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        pass

    tg_id = callback_query.from_user.id
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📊 Статистика пользователей", callback_data="user_stats"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="👥 Управление пользователями", callback_data="user_editor"
        )
    )
    builder.row(
        InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="send_to_alls")
    )
    builder.row(
        InlineKeyboardButton(
            text="🎟️ Управление купонами", callback_data="coupons_editor"
        )
    )
    builder.row(
        InlineKeyboardButton(text="💾 Создать резервную копию", callback_data="backups")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Перезагрузить бота", callback_data="restart_bot")
    )
    await bot.send_message(
        tg_id, "🤖 Панель администратора", reply_markup=builder.as_markup()
    )


async def handle_error(tg_id, callback_query, message):
    await bot.edit_message_text(
        message, chat_id=tg_id, message_id=callback_query.message.message_id
    )
