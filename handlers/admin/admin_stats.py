from datetime import datetime
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from filters.admin import IsAdminFilter
from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_kb
from keyboards.admin.stats_kb import build_stats_kb
from logger import logger
from utils.csv_export import export_payments_csv, export_users_csv

router = Router()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "stats"),
    IsAdminFilter(),
)
async def handle_stats(callback_query: CallbackQuery, session: Any):
    try:
        total_users = await session.fetchval("SELECT COUNT(*) FROM users")
        total_keys = await session.fetchval("SELECT COUNT(*) FROM keys")
        total_referrals = await session.fetchval("SELECT COUNT(*) FROM referrals")

        total_payments_today = int(
            await session.fetchval("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= CURRENT_DATE")
        )
        total_payments_week = int(
            await session.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= date_trunc('week', CURRENT_DATE)"
            )
        )
        total_payments_month = int(
            await session.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= date_trunc('month', CURRENT_DATE)"
            )
        )
        total_payments_all_time = int(await session.fetchval("SELECT COALESCE(SUM(amount), 0) FROM payments"))

        registrations_today = await session.fetchval("SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE")
        registrations_week = await session.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= date_trunc('week', CURRENT_DATE)"
        )
        registrations_month = await session.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= date_trunc('month', CURRENT_DATE)"
        )

        users_updated_today = await session.fetchval("SELECT COUNT(*) FROM users WHERE updated_at >= CURRENT_DATE")

        active_keys = await session.fetchval(
            "SELECT COUNT(*) FROM keys WHERE expiry_time > $1",
            int(datetime.utcnow().timestamp() * 1000),
        )
        expired_keys = total_keys - active_keys
        moscow_tz = pytz.timezone("Europe/Moscow")
        update_time = datetime.now(moscow_tz).strftime("%d.%m.%y %H:%M:%S")

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
            f"   🏦 За все время: <b>{total_payments_all_time} ₽</b>\n\n"
            f" ⏳ Последнее обновление: {update_time}"
        )

        await callback_query.message.edit_text(text=stats_message, reply_markup=build_stats_kb())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):  # skip when Telegram message is not modified
            logger.error(f"Error in user_stats_menu: {e}")
    except Exception as e:
        logger.error(f"Error in user_stats_menu: {e}")


@router.callback_query(
    AdminPanelCallback.filter(F.action == "stats_export_users_csv"),
    IsAdminFilter(),
)
async def handle_export_users_csv(callback_query: CallbackQuery, session: Any):
    kb = build_admin_back_kb("stats")

    try:
        export = await export_users_csv(session)
        await callback_query.message.answer_document(document=export, caption="📥 Экспорт пользователей в CSV")
    except Exception as e:
        logger.error(f"Ошибка при экспорте пользователей в CSV: {e}")
        await callback_query.message.edit_text(text=f"❗ Произошла ошибка при экспорте: {e}", reply_markup=kb)


@router.callback_query(
    AdminPanelCallback.filter(F.action == "stats_export_payments_csv"),
    IsAdminFilter(),
)
async def handle_export_payments_csv(callback_query: CallbackQuery, session: Any):
    kb = build_admin_back_kb("stats")

    try:
        export = await export_payments_csv(session)
        await callback_query.message.answer_document(document=export, caption="📥 Экспорт платежей в CSV")

    except Exception as e:
        logger.error(f"Ошибка при экспорте платежей в CSV: {e}")
        await callback_query.message.edit_text(text=f"❗ Произошла ошибка при экспорте: {e}", reply_markup=kb)
