from datetime import datetime
import subprocess
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from backup import backup_database
from bot import bot
from filters.admin import IsAdminFilter
from logger import logger

router = Router()


class UserEditorState(StatesGroup):
    waiting_for_tg_id = State()
    displaying_user_info = State()
    waiting_for_restart_confirmation = State()
    waiting_for_message = State()


@router.callback_query(F.data == "admin", IsAdminFilter())
async def handle_admin_callback_query(callback_query: CallbackQuery, state: FSMContext):
    await handle_admin_message(callback_query.message, state)


@router.message(Command("admin"), F.data == "admin", IsAdminFilter())
async def handle_admin_message(message: types.Message, state: FSMContext):
    await state.clear()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📊 Статистика пользователей", callback_data="user_stats"))
    builder.row(InlineKeyboardButton(text="👥 Управление пользователями", callback_data="user_editor"))
    builder.row(InlineKeyboardButton(text="🎟️ Управление купонами", callback_data="coupons_editor"))
    builder.row(InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="send_to_alls"))
    builder.row(InlineKeyboardButton(text="💾 Создать резервную копию", callback_data="backups"))
    builder.row(InlineKeyboardButton(text="🔄 Перезагрузить бота", callback_data="restart_bot"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
    await message.answer("🤖 Панель администратора", reply_markup=builder.as_markup())


@router.callback_query(F.data == "user_stats", IsAdminFilter())
async def user_stats_menu(callback_query: CallbackQuery, session: Any):
    try:
        total_users = await session.fetchval("SELECT COUNT(*) FROM connections")
        total_keys = await session.fetchval("SELECT COUNT(*) FROM keys")
        total_referrals = await session.fetchval("SELECT COUNT(*) FROM referrals")

        total_payments_today = await session.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= CURRENT_DATE"
        )
        total_payments_week = await session.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= date_trunc('week', CURRENT_DATE)"
        )
        total_payments_all_time = await session.fetchval("SELECT COALESCE(SUM(amount), 0) FROM payments")

        active_keys = await session.fetchval(
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

        await callback_query.message.answer(stats_message, reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"Error in user_stats_menu: {e}")


@router.callback_query(F.data == "send_to_alls", IsAdminFilter())
async def handle_send_to_all(callback_query: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))
    await callback_query.message.answer(
        "✍️ Введите текст сообщения, который вы хотите отправить всем клиентам 📢🌐:",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(UserEditorState.waiting_for_message)


@router.message(UserEditorState.waiting_for_message, IsAdminFilter())
async def process_message_to_all(message: types.Message, state: FSMContext, session: Any):
    text_message = message.text

    try:
        tg_ids = await session.fetch("SELECT tg_id FROM connections")

        total_users = len(tg_ids)
        success_count = 0
        error_count = 0

        for record in tg_ids:
            tg_id = record["tg_id"]
            try:
                await bot.send_message(chat_id=tg_id, text=text_message)
                success_count += 1
            except Exception as e:
                error_count += 1
                logger.error(f"❌ Ошибка при отправке сообщения пользователю {tg_id}: {e}")

        await message.answer(
            f"📤 Рассылка завершена:\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"✅ Успешно отправлено: {success_count}\n"
            f"❌ Не доставлено: {error_count}"
        )
    except Exception as e:
        logger.error(f"❗ Ошибка при подключении к базе данных: {e}")

    await state.clear()


@router.callback_query(F.data == "backups", IsAdminFilter())
async def handle_backup(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.message.answer("💾 Инициализация резервного копирования базы данных...")
    await backup_database()
    await callback_query.message.answer("✅ Резервная копия успешно создана и отправлена администратору.")


@router.callback_query(F.data == "restart_bot", IsAdminFilter())
async def handle_restart(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(UserEditorState.waiting_for_restart_confirmation)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, перезапустить", callback_data="confirm_restart"),
        InlineKeyboardButton(text="❌ Нет, отмена", callback_data="admin"),
    )
    builder.row(InlineKeyboardButton(text="🔙 Вернуться в меню", callback_data="admin"))
    await callback_query.message.answer(
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
        await callback_query.message.answer("🔄 Бот успешно перезапущен.", reply_markup=builder.as_markup())
    except subprocess.CalledProcessError:
        await callback_query.message.answer("🔄 Бот успешно перезапущен.", reply_markup=builder.as_markup())
    except Exception as e:
        await callback_query.message.answer(
            f"⚠️ Ошибка при перезагрузке бота: {e.stderr}", reply_markup=builder.as_markup()
        )


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
    await callback_query.message.answer("👇 Выберите способ поиска пользователя:", reply_markup=builder.as_markup())
