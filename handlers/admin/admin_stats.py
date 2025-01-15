from datetime import datetime
from io import BytesIO
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, BufferedInputFile

from filters.admin import IsAdminFilter
from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_kb
from keyboards.admin.stats_kb import build_stats_kb
from logger import logger

router = Router()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "stats"),
    IsAdminFilter(),
)
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

        kb = build_stats_kb()

        await callback_query.message.edit_text(
            text=stats_message,
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"Error in user_stats_menu: {e}")


@router.callback_query(
    AdminPanelCallback.filter(F.action == "stats_export_users_csv"),
    IsAdminFilter(),
)
async def export_users_csv(callback_query: CallbackQuery, session: Any):
    kb = build_admin_back_kb("stats")

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
            await callback_query.message.edit_text(
                text="📭 Нет пользователей для экспорта.",
                reply_markup=kb
            )
            return

        csv_data = "tg_id,username,first_name,last_name,language_code,is_bot,balance,trial\n"  # Заголовки CSV
        for user in users:
            csv_data += f"{user['tg_id']},{user['username']},{user['first_name']},{user['last_name']},{user['language_code']},{user['is_bot']},{user['balance']},{user['trial']}\n"

        file_name = BytesIO(csv_data.encode("utf-8-sig"))
        file_name.seek(0)

        file = BufferedInputFile(file_name.getvalue(), filename="users_export.csv")

        await callback_query.message.answer_document(
            document=file,
            caption="📥 Экспорт пользователей в CSV"
        )
        file_name.close()

    except Exception as e:
        logger.error(f"Ошибка при экспорте пользователей в CSV: {e}")
        await callback_query.message.edit_text(
            text=f"❗ Произошла ошибка при экспорте: {e}",
            reply_markup=kb
        )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "stats_export_payments_csv"),
    IsAdminFilter(),
)
async def export_payments_csv(callback_query: CallbackQuery, session: Any):
    kb = build_admin_back_kb("stats")

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
            await callback_query.message.edit_text(
                text="📭 Нет платежей для экспорта.",
                reply_markup=kb
            )
            return

        csv_data = "tg_id,username,first_name,last_name,amount,payment_system,status,created_at\n"  # Заголовки CSV
        for payment in payments:
            csv_data += f"{payment['tg_id']},{payment['username']},{payment['first_name']},{payment['last_name']},{payment['amount']},{payment['payment_system']},{payment['status']},{payment['created_at']}\n"

        file_name = BytesIO(csv_data.encode("utf-8-sig"))
        file_name.seek(0)

        file = BufferedInputFile(file_name.getvalue(), filename="payments_export.csv")

        await callback_query.message.answer_document(
            document=file,
            caption="📥 Экспорт платежей в CSV"
        )
        file_name.close()

    except Exception as e:
        logger.error(f"Ошибка при экспорте платежей в CSV: {e}")
        await callback_query.message.edit_text(
            text=f"❗ Произошла ошибка при экспорте: {e}",
            reply_markup=kb
        )
