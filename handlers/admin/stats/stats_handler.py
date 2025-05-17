from datetime import datetime
from typing import Any
import asyncpg

import pytz

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from filters.admin import IsAdminFilter
from logger import logger
from utils.csv_export import export_hot_leads_csv, export_keys_csv, export_payments_csv, export_users_csv

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import build_stats_kb
from bot import bot
from config import DATABASE_URL, ADMIN_ID

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
        total_payments_yesterday = int(
            await session.fetchval("""
                SELECT COALESCE(SUM(amount), 0)
                FROM payments
                WHERE created_at >= CURRENT_DATE - interval '1 day'
                AND created_at < CURRENT_DATE
            """)
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
        total_payments_last_month = int(
            await session.fetchval("""
                SELECT COALESCE(SUM(amount), 0)
                FROM payments
                WHERE created_at >= date_trunc('month', CURRENT_DATE - interval '1 month')
                AND created_at < date_trunc('month', CURRENT_DATE)
            """)
        )
        total_payments_all_time = int(await session.fetchval("SELECT COALESCE(SUM(amount), 0) FROM payments"))

        registrations_today = await session.fetchval("SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE")
        registrations_yesterday = await session.fetchval("""
            SELECT COUNT(*) FROM users
            WHERE created_at >= CURRENT_DATE - interval '1 day'
            AND created_at < CURRENT_DATE
        """)
        registrations_week = await session.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= date_trunc('week', CURRENT_DATE)"
        )
        registrations_month = await session.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= date_trunc('month', CURRENT_DATE)"
        )
        registrations_last_month = await session.fetchval("""
            SELECT COUNT(*) FROM users
            WHERE created_at >= date_trunc('month', CURRENT_DATE - interval '1 month')
            AND created_at < date_trunc('month', CURRENT_DATE)
        """)

        all_keys = await session.fetch("SELECT created_at, expiry_time FROM keys")

        def count_subscriptions_by_duration(keys):
            periods = {"trial": 0, "1": 0, "3": 0, "6": 0, "12": 0}
            for key in keys:
                try:
                    duration_days = (key["expiry_time"] - key["created_at"]) / (1000 * 60 * 60 * 24)

                    if duration_days <= 29:
                        periods["trial"] += 1
                    elif duration_days <= 89:
                        periods["1"] += 1
                    elif duration_days <= 179:
                        periods["3"] += 1
                    elif duration_days <= 359:
                        periods["6"] += 1
                    else:
                        periods["12"] += 1
                except Exception as e:
                    logger.error(f"Error processing key duration: {e}")
                    continue
            return periods

        subs_all_time = count_subscriptions_by_duration(all_keys)

        users_updated_today = await session.fetchval("SELECT COUNT(*) FROM users WHERE updated_at >= CURRENT_DATE")

        active_keys = await session.fetchval(
            "SELECT COUNT(*) FROM keys WHERE expiry_time > $1",
            int(datetime.utcnow().timestamp() * 1000),
        )
        expired_keys = total_keys - active_keys

        hot_leads_count = await session.fetchval("""
            SELECT COUNT(DISTINCT u.tg_id)
            FROM users u
            JOIN payments p ON u.tg_id = p.tg_id
            LEFT JOIN keys k ON u.tg_id = k.tg_id
            WHERE p.status = 'success'
            AND k.tg_id IS NULL
        """)

        moscow_tz = pytz.timezone("Europe/Moscow")
        update_time = datetime.now(moscow_tz).strftime("%d.%m.%y %H:%M:%S")

        stats_message = (
            "📊 <b>Статистика проекта</b>\n\n"
            "👤 <b>Пользователи:</b>\n"
            f"├ 🗓️ За день: <b>{registrations_today}</b>\n"
            f"├ 🗓️ Вчера: <b>{registrations_yesterday}</b>\n"
            f"├ 📆 За неделю: <b>{registrations_week}</b>\n"
            f"├ 🗓️ За месяц: <b>{registrations_month}</b>\n"
            f"├ 📅 За прошлый месяц: <b>{registrations_last_month}</b>\n"
            f"└ 🌐 Всего: <b>{total_users}</b>\n\n"
            "💡 <b>Активность:</b>\n"
            f"└ 👥 Сегодня были активны: <b>{users_updated_today}</b>\n\n"
            "🤝 <b>Реферальная система:</b>\n"
            f"└ 👥 Всего привлечено: <b>{total_referrals}</b>\n\n"
            "🔐 <b>Подписки:</b>\n"
            f"├ 📦 Всего сгенерировано: <b>{total_keys}</b>\n"
            f"├ ✅ Активных: <b>{active_keys}</b>\n"
            f"├ ❌ Просроченных: <b>{expired_keys}</b>\n"
            f"└ 📋 По срокам:\n"
            f"     • 🎁 Триал: <b>{subs_all_time['trial']}</b>\n"
            f"     • 🗓️ 1 мес: <b>{subs_all_time['1']}</b>\n"
            f"     • 🗓️ 3 мес: <b>{subs_all_time['3']}</b>\n"
            f"     • 🗓️ 6 мес: <b>{subs_all_time['6']}</b>\n"
            f"     • 🗓️ 12 мес: <b>{subs_all_time['12']}</b>\n\n"
            "💰 <b>Финансы:</b>\n"
            f"├ 📅 За день: <b>{total_payments_today} ₽</b>\n"
            f"├ 📆 Вчера: <b>{total_payments_yesterday} ₽</b>\n"
            f"├ 📆 За неделю: <b>{total_payments_week} ₽</b>\n"
            f"├ 📆 За месяц: <b>{total_payments_month} ₽</b>\n"
            f"├ 📆 За прошлый месяц: <b>{total_payments_last_month} ₽</b>\n"
            f"└ 🏦 Всего: <b>{total_payments_all_time} ₽</b>\n\n"
            f"🔥 <b>Горящие лиды</b>: <b>{hot_leads_count}</b> (платили, но не продлили)\n\n"
            f"⏱️ <i>Последнее обновление:</i> <code>{update_time}</code>"
        )

        await callback_query.message.edit_text(text=stats_message, reply_markup=build_stats_kb())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.error(f"Error in user_stats_menu: {e}")
    except Exception as e:
        logger.error(f"Error in user_stats_menu: {e}")
        await callback_query.answer("Произошла ошибка при получении статистики", show_alert=True)


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


@router.callback_query(
    AdminPanelCallback.filter(F.action == "stats_export_hot_leads_csv"),
    IsAdminFilter(),
)
async def handle_export_hot_leads_csv(callback_query: CallbackQuery, session: Any):
    kb = build_admin_back_kb("stats")
    try:
        export = await export_hot_leads_csv(session)
        await callback_query.message.answer_document(document=export, caption="📥 Экспорт горящих лидов")
    except Exception as e:
        logger.error(f"Ошибка при экспорте 'горящих лидов': {e}")
        await callback_query.message.edit_text(text=f"❗ Произошла ошибка при экспорте: {e}", reply_markup=kb)


@router.callback_query(
    AdminPanelCallback.filter(F.action == "stats_export_keys_csv"),
    IsAdminFilter(),
)
async def handle_export_keys_csv(callback_query: CallbackQuery, session: Any):
    kb = build_admin_back_kb("stats")
    try:
        export = await export_keys_csv(session)
        await callback_query.message.answer_document(document=export, caption="📥 Экспорт подписок в CSV")
    except Exception as e:
        logger.error(f"Ошибка при экспорте подписок в CSV: {e}")
        await callback_query.message.edit_text(text=f"❗ Произошла ошибка при экспорте: {e}", reply_markup=kb)


async def send_daily_stats_report():
    try:
        conn = await asyncpg.connect(DATABASE_URL)

        registrations_today = await conn.fetchval("SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE")
        payments_today = int(await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE created_at >= CURRENT_DATE"))
        active_keys = await conn.fetchval("SELECT COUNT(*) FROM keys WHERE expiry_time > $1", int(datetime.utcnow().timestamp() * 1000))

        update_time = datetime.now(pytz.timezone("Europe/Moscow")).strftime("%d.%m.%y %H:%M")

        text = (
            "🗓️ <b>Сводка за день</b>\n\n"
            f"👤 Новых пользователей: <b>{registrations_today}</b>\n"
            f"💰 Оплачено: <b>{payments_today} ₽</b>\n"
            f"🔐 Активных ключей: <b>{active_keys}</b>\n\n"
            f"⏱️ <i>{update_time} МСК</i>"
        )

        for admin_id in ADMIN_ID:
            await bot.send_message(admin_id, text)

        await conn.close()

    except Exception as e:
        logger.error(f"[Stats] Ошибка при отправке ежедневной статистики: {e}")