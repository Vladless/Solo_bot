import subprocess
from datetime import datetime
from io import BytesIO
from typing import Any

import asyncpg
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import DATABASE_URL

from backup import backup_database
from bot import bot
from database import delete_user_data
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

    BOT_VERSION = "4.0.0-preAlpha(9)"

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
        InlineKeyboardButton(text="🤖 Управление Ботом", callback_data="bot_management")
    )
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
    await message.answer(
        f"🤖 Панель администратора\n\nВерсия бота: <b>{BOT_VERSION}</b>",
        reply_markup=builder.as_markup()
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
    builder.row(InlineKeyboardButton(text="🚫 Баны", callback_data="ban_user"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
    await callback_query.message.answer(
        "🤖 Управление ботом",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "user_stats", IsAdminFilter())
async def user_stats_menu(callback_query: CallbackQuery, session: Any):
    try:
        total_users = await session.fetchval("SELECT COUNT(*) FROM users")
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

        registrations_today = await session.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE"
        )
        registrations_week = await session.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= date_trunc('week', CURRENT_DATE)"
        )
        registrations_month = await session.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= date_trunc('month', CURRENT_DATE)"
        )

        users_updated_today = await session.fetchval(
            "SELECT COUNT(*) FROM users WHERE updated_at >= CURRENT_DATE"
        )

        active_keys = await session.fetchval(
            "SELECT COUNT(*) FROM keys WHERE expiry_time > $1",
            int(datetime.utcnow().timestamp() * 1000),
        )
        expired_keys = total_keys - active_keys

        stats_message = (
            f"📊 <b>Подробная статистика проекта:</b>\n\n"
            f"👥 Пользователи:\n"
            f"   📅 За день: <b>{registrations_today}</b>\n"
            f"   📆 За неделю: <b>{registrations_week}</b>\n"
            f"   📆 За месяц: <b>{registrations_month}</b>\n"
            f"   🌐 За все время: <b>{total_users}</b>\n\n"
            f"🌟 Активные пользователи:\n"
            f"   🌟 Активных сегодня: <b>{users_updated_today}</b>\n\n"
            f"👥 Рефералы:\n"
            f"   🤝 Всего привлечено: <b>{total_referrals}</b>\n\n"
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

        csv_data = (
            "tg_id,username,first_name,last_name,language_code,is_bot,balance,trial\n"
        )
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
    builder.row(
        InlineKeyboardButton(text="📢 Отправить всем", callback_data="send_to_all")
    )
    builder.row(
        InlineKeyboardButton(
            text="📢 Отправить с подпиской", callback_data="send_to_subscribed"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📢 Отправить без подписки", callback_data="send_to_unsubscribed"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📢 Рассылка по кластеру", callback_data="send_to_cluster"
        )
    )
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


@router.callback_query(F.data == "send_to_cluster", IsAdminFilter())
async def handle_send_to_cluster(
    callback_query: CallbackQuery, state: FSMContext, session: Any
):
    clusters = await session.fetch("SELECT DISTINCT cluster_name FROM servers")

    builder = InlineKeyboardBuilder()
    for cluster in clusters:
        builder.row(
            InlineKeyboardButton(
                text=f"🌐 {cluster['cluster_name']}",
                callback_data=f"send_cluster|{cluster['cluster_name']}",
            )
        )

    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="send_to"))
    await callback_query.message.answer(
        "✍️ Выберите кластер для рассылки сообщений:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("send_cluster|"), IsAdminFilter())
async def handle_send_cluster(callback_query: CallbackQuery, state: FSMContext):
    cluster_name = callback_query.data.split("|")[1]
    await state.update_data(send_to="cluster", cluster_name=cluster_name)
    await callback_query.message.answer(
        f"✍️ Введите текст сообщения для рассылки пользователям кластера <b>{cluster_name}</b>:"
    )
    await state.set_state(UserEditorState.waiting_for_message)


@router.message(UserEditorState.waiting_for_message, IsAdminFilter())
async def process_message_to_all(
    message: types.Message, state: FSMContext, session: Any
):
    text_message = message.text

    try:
        state_data = await state.get_data()
        send_to = state_data.get("send_to", "all")

        if send_to == "all":
            tg_ids = await session.fetch("SELECT DISTINCT tg_id FROM connections")
        elif send_to == "subscribed":
            tg_ids = await session.fetch(
                """
                SELECT DISTINCT c.tg_id 
                FROM connections c
                JOIN keys k ON c.tg_id = k.tg_id
                WHERE k.expiry_time > $1
            """,
                int(datetime.utcnow().timestamp() * 1000),
            )
        elif send_to == "unsubscribed":
            tg_ids = await session.fetch(
                """
                SELECT c.tg_id 
                FROM connections c
                LEFT JOIN keys k ON c.tg_id = k.tg_id
                GROUP BY c.tg_id
                HAVING COUNT(k.tg_id) = 0 OR MAX(k.expiry_time) <= $1
            """,
                int(datetime.utcnow().timestamp() * 1000),
            )
        elif send_to == "cluster":
            cluster_name = state_data.get("cluster_name")
            tg_ids = await session.fetch(
                """
                SELECT DISTINCT c.tg_id
                FROM connections c
                JOIN keys k ON c.tg_id = k.tg_id
                JOIN servers s ON k.server_id = s.cluster_name
                WHERE s.cluster_name = $1
            """,
                cluster_name,
            )

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


@router.callback_query(F.data == "ban_user")
async def handle_ban_user(callback_query: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📄 Выгрузить в CSV", callback_data="export_to_csv")
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑️ Удалить из БД", callback_data="delete_banned_users"
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_management"))
    await callback_query.message.answer(
        "🚫 Заблокировавшие бота\n\n"
        "Здесь можно просматривать и удалять пользователей, которые забанили вашего бота!",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "export_to_csv")
async def export_banned_users_to_csv(callback_query: types.CallbackQuery):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        banned_users = await conn.fetch("SELECT tg_id, blocked_at FROM blocked_users")

        import csv
        import io

        csv_output = io.StringIO()
        writer = csv.writer(csv_output)
        writer.writerow(["tg_id", "blocked_at"])
        for user in banned_users:
            writer.writerow([user["tg_id"], user["blocked_at"]])

        csv_output.seek(0)

        document = BufferedInputFile(
            file=csv_output.getvalue().encode("utf-8"), filename="banned_users.csv"
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_management")
        )

        await callback_query.message.answer_document(
            document=document,
            caption="📄 Список заблокировавших бота пользователей",
            reply_markup=builder.as_markup(),
        )
    except Exception as e:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_management")
        )
        await callback_query.message.answer(
            text=f"Ошибка при выгрузке CSV: {e}",
            reply_markup=builder.as_markup(),
        )
    finally:
        await conn.close()


@router.callback_query(F.data == "delete_banned_users")
async def delete_banned_users(callback_query: types.CallbackQuery):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        blocked_users = await conn.fetch("SELECT tg_id FROM blocked_users")
        blocked_ids = [record["tg_id"] for record in blocked_users]

        if not blocked_ids:
            await callback_query.message.answer(
                "📂 Нет заблокировавших пользователей для удаления."
            )
            return

        for tg_id in blocked_ids:
            await delete_user_data(conn, tg_id)

        await conn.execute(
            "DELETE FROM blocked_users WHERE tg_id = ANY($1)", blocked_ids
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_management")
        )
        await callback_query.message.answer(
            text=f"🗑️ Удалено данные о {len(blocked_ids)} пользователях и связанных записях.",
            reply_markup=builder.as_markup(),
        )
    except Exception as e:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_management")
        )
        await callback_query.message.answer(
            text=f"Ошибка при удалении записей: {e}",
            reply_markup=builder.as_markup(),
        )
    finally:
        await conn.close()
