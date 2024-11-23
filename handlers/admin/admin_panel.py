from datetime import datetime
import subprocess

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncpg

from backup import backup_database
from bot import bot
from config import DATABASE_URL
from filters.admin import IsAdminFilter
from handlers.admin.admin_commands import send_message_to_all_clients

router = Router()


class UserEditorState(StatesGroup):
    waiting_for_tg_id = State()
    displaying_user_info = State()
    waiting_for_restart_confirmation = State()


@router.callback_query(F.data == "admin", IsAdminFilter())
async def handle_admin_callback_query(callback_query: CallbackQuery, state: FSMContext):
    await handle_admin_message(callback_query.message, state)


@router.message(Command("admin"), F.data == "admin", IsAdminFilter())
async def handle_admin_message(message: types.Message, state: FSMContext):
    await state.clear()

    try:
        await message.delete()
    except Exception:
        pass

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика пользователей", callback_data="user_stats"))
    builder.row(InlineKeyboardButton(text="👥 Управление пользователями", callback_data="user_editor"))
    builder.row(InlineKeyboardButton(text="🎟️ Управление купонами", callback_data="coupons_editor"))
    builder.row(InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="send_to_alls"))
    builder.row(InlineKeyboardButton(text="💾 Создать резервную копию", callback_data="backups"))
    builder.row(InlineKeyboardButton(text="🔄 Перезагрузить бота", callback_data="restart_bot"))
    builder.row(InlineKeyboardButton(text="⬅️ Вернуться в профиль", callback_data="view_profile"))
    await bot.send_message(
        message.chat.id,
        "🤖 Панель администратора",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "user_stats", IsAdminFilter())
async def user_stats_menu(callback_query: CallbackQuery):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM connections")
        total_keys = await conn.fetchval("SELECT COUNT(*) FROM keys")
        total_referrals = await conn.fetchval("SELECT COUNT(*) FROM referrals")

        total_payments_today = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= CURRENT_DATE"
        )
        total_payments_week = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= date_trunc('week', CURRENT_DATE)"
        )
        total_payments_all_time = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM payments")

        active_keys = await conn.fetchval(
            "SELECT COUNT(*) FROM keys WHERE expiry_time > $1",
            int(datetime.utcnow().timestamp() * 1000),
        )
        expired_keys = total_keys - active_keys

        stats_message = (
            f"📊 <b>Подробная статистика проекта:</b>\n\n"
            f"👥 Пользователи:\n"
            f"   🌐 Зарегистрировано: <b>{total_users}</b>\n"
            f"   🤝 Привлеченных рефералов: <b>{total_referrals}</b>\n\n"
            f"🔑 Ключи:\n"
            f"   🌈 Всего сгенерировано: <b>{total_keys}</b>\n"
            f"   ✅ Действующих: <b>{active_keys}</b>\n"
            f"   ❌ Просроченных: <b>{expired_keys}</b>\n\n"
            f"💰 Финансовая статистика:\n"
            f"   📅 За день: <b>{total_payments_today} ₽</b>\n"
            f"   📆 За неделю: <b>{total_payments_week} ₽</b>\n"
            f"   🏦 За все время: <b>{total_payments_all_time} ₽</b>\n"
        )

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔄 Обновить", callback_data="user_stats"))
        builder.row(InlineKeyboardButton(text="🔙 Вернуться в меню", callback_data="admin"))

        await callback_query.message.edit_text(stats_message, reply_markup=builder.as_markup(), parse_mode="HTML")
    finally:
        await conn.close()


@router.callback_query(F.data == "send_to_alls", IsAdminFilter())
async def handle_send_to_all(callback_query: CallbackQuery, state: FSMContext):
    await send_message_to_all_clients(callback_query.message, state, from_panel=True)
    await callback_query.answer()


@router.callback_query(F.data == "backups", IsAdminFilter())
async def handle_backup(message: Message):
    await message.answer("💾 Инициализация резервного копирования базы данных...")
    await backup_database()
    await message.answer("✅ Резервная копия успешно создана и отправлена администратору.")


@router.callback_query(F.data == "restart_bot", IsAdminFilter())
async def handle_restart(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(UserEditorState.waiting_for_restart_confirmation)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, перезапустить", callback_data="confirm_restart"),
        InlineKeyboardButton(text="❌ Нет, отмена", callback_data="admin"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Вернуться в меню", callback_data="admin"))
    await callback_query.message.edit_text(
        "🤔 Вы уверены, что хотите перезапустить бота?",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(
    F.data == "confirm_restart",
    UserEditorState.waiting_for_restart_confirmation,
    IsAdminFilter(),
)
async def confirm_restart_bot(callback_query: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Вернуться в меню", callback_data="admin"))
    try:
        subprocess.run(
            ["systemctl", "restart", "bot.service"],
            check=True,
            capture_output=True,
            text=True,
        )
        await state.clear()
        await callback_query.message.edit_text("🔄 Бот успешно перезапущен.", reply_markup=builder.as_markup())
    except subprocess.CalledProcessError:
        await callback_query.message.edit_text("🔄 Бот успешно перезапущен.", reply_markup=builder.as_markup())
    except Exception as e:
        await callback_query.message.edit_text(
            f"⚠️ Ошибка при перезагрузке бота: {e.stderr}",
            reply_markup=builder.as_markup(),
        )
    finally:
        await callback_query.answer()


@router.callback_query(F.data == "user_editor", IsAdminFilter())
async def user_editor_menu(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔍 Поиск по названию ключа",
            callback_data="search_by_key_name",
        )
    )
    builder.row(InlineKeyboardButton(text="🆔 Поиск по Telegram ID", callback_data="search_by_tg_id"))
    builder.row(InlineKeyboardButton(text="🌐 Поиск по Username", callback_data="search_by_username"))
    builder.row(InlineKeyboardButton(text="🔙 Вернуться назад", callback_data="admin"))
    await callback_query.message.edit_text(
        "👇 Выберите способ поиска пользователя:",
        reply_markup=builder.as_markup(),
    )


async def handle_error(tg_id, callback_query, message):
    await bot.edit_message_text(message, chat_id=tg_id, message_id=callback_query.message.message_id)
