import subprocess
from datetime import datetime
from io import BytesIO
from typing import Any

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton
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

    BOT_VERSION = "3.2.4-beta"  # Укажите текущую версию бота

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
            text="🖥️ Управление серверами", callback_data="servers_editor"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🎟️ Управление купонами", callback_data="coupons_editor"
        )
    )
    builder.row(
        InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="send_to")
    )
    builder.row(
        InlineKeyboardButton(
            text="🤖 Управление Ботом", callback_data="bot_management"
        )
    )
    builder.row(
        InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")
    )
    await message.answer(
        f"🤖 Панель администратора\n\nВерсия бота: <b>{BOT_VERSION}</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "bot_management")
async def handle_bot_management(callback_query: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💾 Создать резервную копию", callback_data="backups")
    )
    builder.row(
        InlineKeyboardButton(text="🔄 Перезагрузить бота", callback_data="restart_bot")
    )
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")
    )
    await callback_query.message.answer(
        "🤖 Управление ботом",
        reply_markup=builder.as_markup(),
    )


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
        total_payments_month = await session.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= date_trunc('month', CURRENT_DATE)"
        )
        total_payments_all_time = await session.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM payments"
        )

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
            f"   📆 За месяц: <b>{total_payments_month} ₽</b>\n"
            f"   🏦 За все время: <b>{total_payments_all_time} ₽</b>\n"
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="🔄 Обновить", callback_data="user_stats")
        )
        builder.row(
            InlineKeyboardButton(
                text="📥 Выгрузить пользователей в CSV",
                callback_data="export_users_csv",
            )
        )
        builder.row(
            InlineKeyboardButton(
                text="📥 Выгрузить оплаты в CSV", callback_data="export_payments_csv"
            )
        )
        builder.row(
            InlineKeyboardButton(text="🔙 Вернуться в меню", callback_data="admin")
        )

        await callback_query.message.answer(
            stats_message, reply_markup=builder.as_markup()
        )
    except Exception as e:
        logger.error(f"Error in user_stats_menu: {e}")


@router.callback_query(F.data == "export_users_csv", IsAdminFilter())
async def export_users_csv(callback_query: CallbackQuery, session: Any):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_stats"))
    try:
        users = await session.fetch(
            """
            SELECT 
                u.tg_id, 
                u.username, 
                u.first_name, 
                u.last_name, 
                u.language_code, 
                u.is_bot, 
                c.balance, 
                c.trial 
            FROM users u
            LEFT JOIN connections c ON u.tg_id = c.tg_id
        """
        )

        if not users:
            await callback_query.message.answer(
                "📭 Нет пользователей для экспорта.", reply_markup=builder.as_markup()
            )
            return

        csv_data = "tg_id,username,first_name,last_name,language_code,is_bot,balance,trial\n"
        for user in users:
            csv_data += f"{user['tg_id']},{user['username']},{user['first_name']},{user['last_name']},{user['language_code']},{user['is_bot']},{user['balance']},{user['trial']}\n"

        file_name = BytesIO(csv_data.encode("utf-8-sig"))
        file_name.seek(0)

        file = BufferedInputFile(file_name.getvalue(), filename="users_export.csv")

        await callback_query.message.answer_document(
            file,
            caption="📥 Экспорт пользователей в CSV",
            reply_markup=builder.as_markup(),
        )
        file_name.close()

    except Exception as e:
        logger.error(f"Ошибка при экспорте пользователей в CSV: {e}")
        await callback_query.message.answer(
            "❗ Произошла ошибка при экспорте пользователей.",
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "export_payments_csv", IsAdminFilter())
async def export_payments_csv(callback_query: CallbackQuery, session: Any):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="user_stats"))
    try:
        payments = await session.fetch(
            """
            SELECT 
                u.tg_id, 
                u.username, 
                u.first_name, 
                u.last_name, 
                p.amount, 
                p.payment_system,
                p.status,
                p.created_at 
            FROM users u
            JOIN payments p ON u.tg_id = p.tg_id
        """
        )

        if not payments:
            await callback_query.message.answer(
                "📭 Нет платежей для экспорта.", reply_markup=builder.as_markup()
            )
            return

        csv_data = "tg_id,username,first_name,last_name,amount,payment_system,status,created_at\n"  # Заголовки CSV
        for payment in payments:
            csv_data += f"{payment['tg_id']},{payment['username']},{payment['first_name']},{payment['last_name']},{payment['amount']},{payment['payment_system']},{payment['status']},{payment['created_at']}\n"

        file_name = BytesIO(csv_data.encode("utf-8-sig"))
        file_name.seek(0)

        file = BufferedInputFile(file_name.getvalue(), filename="payments_export.csv")

        await callback_query.message.answer_document(
            file, caption="📥 Экспорт платежей в CSV", reply_markup=builder.as_markup()
        )
        file_name.close()

    except Exception as e:
        logger.error(f"Ошибка при экспорте платежей в CSV: {e}")
        await callback_query.message.answer(
            "❗ Произошла ошибка при экспорте платежей.",
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "send_to", IsAdminFilter())
async def handle_send_to_all(callback_query: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📢 Отправить всем", callback_data="send_to_all"))
    builder.row(InlineKeyboardButton(text="📢 Отправить с подпиской", callback_data="send_to_subscribed"))
    builder.row(InlineKeyboardButton(text="📢 Отправить без подписки", callback_data="send_to_unsubscribed"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin"))
    await callback_query.message.answer(
        "✍️ Выберите группу пользователей и введите текст сообщения для рассылки:",
        reply_markup=builder.as_markup(),
    )

@router.callback_query(F.data == "send_to_all", IsAdminFilter())
async def handle_send_to_all(callback_query: CallbackQuery, state: FSMContext):
    await state.update_data(send_to="all")
    await callback_query.message.answer(
        "✍️ Введите текст сообщения для рассылки всем пользователям:"
    )
    await state.set_state(UserEditorState.waiting_for_message)

@router.callback_query(F.data == "send_to_subscribed", IsAdminFilter())
async def handle_send_to_subscribed(callback_query: CallbackQuery, state: FSMContext):
    await state.update_data(send_to="subscribed")
    await callback_query.message.answer(
        "✍️ Введите текст сообщения для рассылки пользователям с активной подпиской:"
    )
    await state.set_state(UserEditorState.waiting_for_message)

@router.callback_query(F.data == "send_to_unsubscribed", IsAdminFilter())
async def handle_send_to_unsubscribed(callback_query: CallbackQuery, state: FSMContext):
    await state.update_data(send_to="unsubscribed")
    await callback_query.message.answer(
        "✍️ Введите текст сообщения для рассылки пользователям без активной подписки:"
    )
    await state.set_state(UserEditorState.waiting_for_message)


@router.message(UserEditorState.waiting_for_message, IsAdminFilter())
async def process_message_to_all(
    message: types.Message, state: FSMContext, session: Any
):
    text_message = message.text

    try:
        state_data = await state.get_data()
        send_to = state_data.get('send_to', 'all')

        if send_to == 'all':
            tg_ids = await session.fetch("SELECT DISTINCT tg_id FROM connections")
        elif send_to == 'subscribed':
            tg_ids = await session.fetch("""
                SELECT DISTINCT c.tg_id 
                FROM connections c
                JOIN keys k ON c.tg_id = k.tg_id
                WHERE k.expiry_time > $1
            """, int(datetime.utcnow().timestamp() * 1000))
        elif send_to == 'unsubscribed':
            tg_ids = await session.fetch("""
                SELECT c.tg_id 
                FROM connections c
                LEFT JOIN keys k ON c.tg_id = k.tg_id
                GROUP BY c.tg_id
                HAVING COUNT(k.tg_id) = 0 OR MAX(k.expiry_time) <= $1
            """, int(datetime.utcnow().timestamp() * 1000))

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
                logger.error(
                    f"❌ Ошибка при отправке сообщения пользователю {tg_id}: {e}"
                )

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
    await callback_query.message.answer(
        "💾 Инициализация резервного копирования базы данных..."
    )
    await backup_database()
    await callback_query.message.answer(
        "✅ Резервная копия успешно создана и отправлена администратору."
    )


@router.callback_query(F.data == "restart_bot", IsAdminFilter())
async def handle_restart(callback_query: CallbackQuery, state: FSMContext):
    await state.set_state(UserEditorState.waiting_for_restart_confirmation)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Да, перезапустить", callback_data="confirm_restart"
        ),
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
            ["sudo", "systemctl", "restart", "bot.service"],
            check=True,
            capture_output=True,
            text=True,
        )
        await state.clear()
        await callback_query.message.answer(
            "🔄 Бот успешно перезапущен.", reply_markup=builder.as_markup()
        )
    except subprocess.CalledProcessError:
        await callback_query.message.answer(
            "🔄 Бот успешно перезапущен.", reply_markup=builder.as_markup()
        )
    except Exception as e:
        await callback_query.message.answer(
            f"⚠️ Ошибка при перезагрузке бота: {e.stderr}",
            reply_markup=builder.as_markup(),
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
    builder.row(
        InlineKeyboardButton(
            text="🆔 Поиск по Telegram ID", callback_data="search_by_tg_id"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🌐 Поиск по Username", callback_data="search_by_username"
        )
    )
    builder.row(InlineKeyboardButton(text="🔙 Вернуться назад", callback_data="admin"))
    await callback_query.message.answer(
        "👇 Выберите способ поиска пользователя:", reply_markup=builder.as_markup()
    )
