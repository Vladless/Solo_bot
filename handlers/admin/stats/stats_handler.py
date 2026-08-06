from collections import Counter
from datetime import date, datetime, timedelta
from html import escape

import pytz

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import (
    AUDIT_STEP_LABELS,
    clear_audit_redis_buffers,
    get_audit_db_reset_at,
    get_audit_funnel,
    get_audit_funnel_from_redis,
    get_audit_stats,
    get_audit_stats_from_redis,
    set_audit_db_reset_at,
)
from bot import bot
from database import (
    count_active_keys,
    count_active_paid_keys,
    count_active_trial_keys,
    count_hot_leads,
    count_identities_with_email,
    count_total_keys,
    count_total_referrals,
    count_total_users,
    count_users_registered_between,
    count_users_registered_since,
    count_users_updated_today,
    count_users_with_tg_id,
    get_tariff_distribution,
    get_tariff_names_groups_subgroups_durations,
    sum_payments_between,
    sum_payments_since,
    sum_total_payments,
)
from filters.admin import HasPermission, IsAdminFilter
from filters.permissions import PERM_STATS
from hooks.hooks import run_hooks
from logger import logger
from settings.config import ADMIN_ID
from utils.csv_export import (
    export_hot_leads_csv,
    export_keys_csv,
    export_payments_csv,
    export_users_csv,
)

from ..panel.headers import card, menu_text, menu_title, quote, section
from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import (
    build_audit_refresh_kb,
    build_audit_reset_confirm_kb,
    build_audit_source_kb,
    build_stats_charts_kb,
    build_stats_kb,
)


router = Router()
router.callback_query.filter(HasPermission(PERM_STATS))
router.message.filter(HasPermission(PERM_STATS))

KEY_AUDIT_STEPS_ORDER = (
    "start",
    "start_coupon",
    "start_gift",
    "start_referral",
    "start_utm",
    "profile",
    "about",
    "instructions",
    "balance",
    "view_keys",
    "buy_entry",
    "tariff_config",
    "key_create",
    "pay_start",
    "pay",
    "key_view",
    "connect",
    "key_manage",
    "renew",
    "addons",
    "referral",
    "coupons",
)


def _previous_moscow_day_window(now: datetime | None = None) -> tuple[datetime, datetime, date]:
    moscow_tz = pytz.timezone("Europe/Moscow")
    current = now or datetime.now(moscow_tz)
    yesterday_date = current.date() - timedelta(days=1)
    start = moscow_tz.localize(datetime.combine(yesterday_date, datetime.min.time()))
    end = start + timedelta(days=1)
    return start.astimezone(pytz.UTC), end.astimezone(pytz.UTC), yesterday_date


def _audit_success_event_counts(by_path: list[dict]) -> dict[str, int]:
    """Успешные события по шагам для бизнес-метрик в отчёте."""
    return {row["step"]: int(row.get("success", 0) or 0) for row in by_path}


@router.callback_query(AdminPanelCallback.filter(F.action == "stats"), IsAdminFilter(), flags={"popup": True})
async def handle_stats(callback_query: CallbackQuery, session: AsyncSession):
    try:
        moscow_tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(moscow_tz)
        today = now.date()

        today_start = moscow_tz.localize(datetime.combine(today, datetime.min.time()))
        today_start_utc = today_start.astimezone(pytz.UTC).replace(tzinfo=None)

        yesterday_date = today - timedelta(days=1)
        yesterday_start = moscow_tz.localize(datetime.combine(yesterday_date, datetime.min.time()))
        yesterday_end = moscow_tz.localize(datetime.combine(today, datetime.min.time()))
        yesterday_start_utc = yesterday_start.astimezone(pytz.UTC).replace(tzinfo=None)
        yesterday_end_utc = yesterday_end.astimezone(pytz.UTC).replace(tzinfo=None)

        week_start_date = today - timedelta(days=today.weekday())
        week_start = moscow_tz.localize(datetime.combine(week_start_date, datetime.min.time()))
        week_start_utc = week_start.astimezone(pytz.UTC).replace(tzinfo=None)

        month_start_date = today.replace(day=1)
        month_start = moscow_tz.localize(datetime.combine(month_start_date, datetime.min.time()))
        month_start_utc = month_start.astimezone(pytz.UTC).replace(tzinfo=None)

        last_month_start_date = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        this_month_start_date = today.replace(day=1)
        last_month_start = moscow_tz.localize(datetime.combine(last_month_start_date, datetime.min.time()))
        last_month_end = moscow_tz.localize(datetime.combine(this_month_start_date, datetime.min.time()))
        last_month_start_utc = last_month_start.astimezone(pytz.UTC).replace(tzinfo=None)
        last_month_end_utc = last_month_end.astimezone(pytz.UTC).replace(tzinfo=None)

        total_users = await count_total_users(session)
        users_with_tg = await count_users_with_tg_id(session)
        identities_with_email = await count_identities_with_email(session)
        users_updated_today = await count_users_updated_today(session, today_start_utc)
        registrations_today = await count_users_registered_since(session, today_start_utc)
        registrations_yesterday = await count_users_registered_between(session, yesterday_start_utc, yesterday_end_utc)
        registrations_week = await count_users_registered_since(session, week_start_utc)
        registrations_month = await count_users_registered_since(session, month_start_utc)
        registrations_last_month = await count_users_registered_between(
            session, last_month_start_utc, last_month_end_utc
        )
        total_keys = await count_total_keys(session)
        active_keys = await count_active_keys(session)
        active_paid_keys = await count_active_paid_keys(session)
        active_trial_keys = await count_active_trial_keys(session)
        tariff_counts, no_tariff_keys = await get_tariff_distribution(session, include_unbound=True)

        expired_keys = total_keys - active_keys
        tariff_ids = [tid for tid, _ in tariff_counts]
        (
            tariff_names,
            tariff_groups,
            tariff_subgroups,
            tariff_durations,
        ) = await get_tariff_names_groups_subgroups_durations(session, tariff_ids)

        grouped_tariffs = {}
        for tid, count in tariff_counts:
            group = tariff_groups.get(tid, "unknown")
            subgroup = tariff_subgroups.get(tid)
            if group not in grouped_tariffs:
                grouped_tariffs[group] = {}
            if subgroup not in grouped_tariffs[group]:
                grouped_tariffs[group][subgroup] = []
            grouped_tariffs[group][subgroup].append((tid, count))

        tariff_stats_text = ""
        duration_buckets = Counter()
        now_ts = int(now.timestamp() * 1000)

        for key in no_tariff_keys:
            duration_days = round((key["expiry_time"] - now_ts) / (1000 * 60 * 60 * 24))
            if 25 <= duration_days <= 35:
                bucket = "Без тарифа: 1 мес"
            elif 80 <= duration_days <= 100:
                bucket = "Без тарифа: 3 мес"
            elif 170 <= duration_days <= 200:
                bucket = "Без тарифа: 6 мес"
            elif 350 <= duration_days <= 380:
                bucket = "Без тарифа: 12 мес"
            else:
                bucket = "Без тарифа: прочее"
            duration_buckets[bucket] += 1

        bucket_order = {
            "Без тарифа: 1 мес": 1,
            "Без тарифа: 3 мес": 2,
            "Без тарифа: 6 мес": 3,
            "Без тарифа: 12 мес": 4,
            "Без тарифа: прочее": 5,
        }
        sorted_buckets = sorted(duration_buckets.items(), key=lambda x: bucket_order.get(x[0], 999))

        for name, count in sorted_buckets:
            tariff_stats_text += f"{name}: {count}\n"

        for _group_idx, (group, subgroups_dict) in enumerate(grouped_tariffs.items()):
            group_total = 0
            for tariffs_list in subgroups_dict.values():
                group_total += sum(count for _, count in tariffs_list)

            tariff_stats_text += f"{group}: {group_total}\n"
            sorted_subgroups = sorted(subgroups_dict.items(), key=lambda x: (x[0] is None, x[0] or ""))
            for subgroup, tariffs in sorted_subgroups:
                sorted_tariffs = sorted(tariffs, key=lambda x: tariff_durations.get(x[0], 0))
                subgroup_total = sum(count for _, count in sorted_tariffs)

                if subgroup:
                    tariff_stats_text += f"  {subgroup}: {subgroup_total}\n"

                for tid, count in sorted_tariffs:
                    name = tariff_names.get(tid, f"ID {tid}")
                    tariff_stats_text += f"{'  ' if subgroup else ''}{name}: {count}\n"

        tariff_rows = [row for row in tariff_stats_text.split("\n") if row.strip()]

        total_referrals = await count_total_referrals(session)

        total_payments_today = await sum_payments_since(session, today_start.replace(tzinfo=None))
        total_payments_yesterday = await sum_payments_between(
            session, yesterday_start.replace(tzinfo=None), yesterday_end.replace(tzinfo=None)
        )
        total_payments_week = await sum_payments_since(session, week_start.replace(tzinfo=None))
        total_payments_month = await sum_payments_since(session, month_start.replace(tzinfo=None))
        total_payments_last_month = await sum_payments_between(
            session, last_month_start.replace(tzinfo=None), last_month_end.replace(tzinfo=None)
        )
        total_payments_all_time = await sum_total_payments(session)
        hot_leads_count = await count_hot_leads(session)

        update_time = now.strftime("%d.%m.%y %H:%M:%S")

        stats_body = card(
            section(
                "👤 Клиенты",
                f"Сегодня: {registrations_today}",
                f"Вчера: {registrations_yesterday}",
                f"Неделя: {registrations_week}",
                f"Месяц: {registrations_month}",
                f"Прошлый: {registrations_last_month}",
                f"Всего: {total_users}",
            ),
            section(
                "🔗 Аккаунты",
                f"С почтой: {identities_with_email}",
                f"С Telegram: {users_with_tg}",
                f"Активны сегодня: {users_updated_today}",
                f"Привлечено: {total_referrals}",
            ),
            section(
                "🔐 Подписки",
                f"Всего: {total_keys}",
                f"Активных: {active_keys}",
                f"Платных: {active_paid_keys}",
                f"Пробных: {active_trial_keys}",
                f"Просрочено: {expired_keys}",
            ),
            section(
                "💰 Деньги",
                f"Сегодня: {total_payments_today} ₽",
                f"Вчера: {total_payments_yesterday} ₽",
                f"Неделя: {total_payments_week} ₽",
                f"Месяц: {total_payments_month} ₽",
                f"Прошлый: {total_payments_last_month} ₽",
                f"Всего: {total_payments_all_time} ₽",
            ),
            section("🔥 Горячие лиды", str(hot_leads_count)),
            section("📦 По тарифам", *tariff_rows) if tariff_rows else "",
            section("⏱ Обновлено", update_time),
        )
        extra_blocks = await run_hooks("admin_stats", session=session, now=now)
        if extra_blocks:
            stats_body += "\n\n" + "\n\n".join([str(b) for b in extra_blocks if b])

        new_kb = build_stats_kb()
        stats_message = menu_title("Статистика проекта") + "\n\n" + stats_body
        current_text = callback_query.message.html_text or callback_query.message.text or ""
        cur_kb = callback_query.message.reply_markup
        cur_kb_json = cur_kb.model_dump_json() if cur_kb else None
        new_kb_json = new_kb.model_dump_json() if new_kb else None

        if current_text == stats_message and cur_kb_json == new_kb_json:
            try:
                await callback_query.answer()
            except Exception:
                pass
        else:
            await callback_query.message.edit_text(text=stats_message, reply_markup=new_kb)

    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logger.error(f"Error in user_stats_menu: {e}")
    except Exception as e:
        logger.error(f"Error in user_stats_menu: {e}")
        await callback_query.answer("Не удалось собрать статистику", show_alert=True)


async def _build_stats_chart(session: AsyncSession, period: int):
    from database.subscription_events import get_subscription_dynamics
    from handlers.admin.stats.report_charts import chart_legend, render_stats_chart

    moscow_tz = pytz.timezone("Europe/Moscow")
    today = datetime.now(moscow_tz).date()
    dates = [today - timedelta(days=i) for i in range(period - 1, -1, -1)]
    labels = [d.strftime("%d") for d in dates]

    users_series: list[float] = []
    revenue_series: list[float] = []
    for d in dates:
        users, revenue = await _day_users_revenue(session, moscow_tz, d)
        users_series.append(float(users))
        revenue_series.append(float(revenue))

    dyn = await get_subscription_dynamics(session, period)
    ev_map = {e["date"]: e for e in dyn.get("dailyEvents", [])}
    active_map = {a["date"]: a["active"] for a in dyn.get("activeTrend", [])}
    created_series = [float((ev_map.get(d.strftime("%Y-%m-%d")) or {}).get("created", 0)) for d in dates]
    renewed_series = [float((ev_map.get(d.strftime("%Y-%m-%d")) or {}).get("renewed", 0)) for d in dates]
    expired_series = [float((ev_map.get(d.strftime("%Y-%m-%d")) or {}).get("expired", 0)) for d in dates]
    net_series = [created_series[i] - expired_series[i] for i in range(len(dates))]
    active_series = [float(active_map.get(d.strftime("%Y-%m-%d"), 0)) for d in dates]

    panels = [
        {"name": "Доход, руб/день", "color": (63, 185, 80), "values": revenue_series},
        {"name": "Новые пользователи/день", "color": (88, 166, 255), "values": users_series},
        {"name": "Новые подписки/день", "color": (191, 135, 255), "values": created_series},
        {"name": "Продления/день", "color": (240, 160, 70), "values": renewed_series},
        {"name": "Отток подписок/день", "color": (240, 90, 90), "values": expired_series},
        {"name": "Прирост базы (новые − отток)", "color": (45, 200, 160), "values": net_series},
    ]
    if any(active_series):
        panels.append({"name": "Активные подписки", "color": (244, 114, 182), "values": active_series})

    return render_stats_chart(labels, panels), chart_legend(panels)


@router.callback_query(AdminPanelCallback.filter(F.action == "stats_charts_close"), IsAdminFilter())
async def handle_stats_charts_close(callback_query: CallbackQuery):
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    await callback_query.answer()


@router.callback_query(AdminPanelCallback.filter(F.action.startswith("stats_chart")), IsAdminFilter())
async def handle_stats_charts(callback_query: CallbackQuery, callback_data: AdminPanelCallback, session: AsyncSession):
    action = callback_data.action
    period = 30
    if action.startswith("stats_chartp_"):
        try:
            period = int(action.rsplit("_", 1)[1])
        except ValueError:
            period = 30
    if period not in (7, 30, 90):
        period = 30

    await callback_query.answer()

    try:
        chart, legend = await _build_stats_chart(session, period)
        if chart is None:
            await callback_query.message.answer(menu_text("Статистика", "❌ Не удалось построить график."))
            return

        photo = BufferedInputFile(chart.getvalue(), filename="stats.png")
        caption = f"📊 <b>Динамика за {period} дн.</b>\n{legend}"
        kb = build_stats_charts_kb(period)
        msg = callback_query.message
        if msg.photo:
            from aiogram.types import InputMediaPhoto

            await msg.edit_media(InputMediaPhoto(media=photo, caption=caption), reply_markup=kb)
        else:
            await msg.answer_photo(photo=photo, caption=caption, reply_markup=kb)
    except Exception as e:
        logger.error(f"[Stats] Ошибка построения графиков: {e}")


async def _build_audit_report(session: AsyncSession, source: str = "db") -> tuple[str | None, str | None]:
    """Собирает текст отчёта аудита из выбранного источника."""
    try:
        moscow_tz = pytz.timezone("Europe/Moscow")
        now = datetime.now(moscow_tz)
        reset_note = ""
        if source == "redis":
            stats = await get_audit_stats_from_redis(max_events=5000)
            funnel = await get_audit_funnel_from_redis(max_events=5000)
            if stats is None or funnel is None:
                return (None, "Буфер Redis для аудита выключен.")
            summary = stats["summary"]
            by_path = stats["by_path"]
            source_note = "Redis, без добора из БД"
        else:
            start_utc, end_utc, report_date = _previous_moscow_day_window(now)
            reset_at = await get_audit_db_reset_at(session)
            effective_start_utc = start_utc
            if reset_at is not None:
                effective_start_utc = max(start_utc, reset_at.astimezone(pytz.UTC))
            funnel = await get_audit_funnel(session, date_from=effective_start_utc, date_to=end_utc)
            stats = await get_audit_stats(session, date_from=effective_start_utc, date_to=end_utc)
            if effective_start_utc != start_utc:
                reset_note = reset_at.astimezone(moscow_tz).strftime("%d.%m.%y %H:%M")
            summary = stats["summary"]
            by_path = stats["by_path"]
            source_note = f"БД за {report_date.strftime('%d.%m.%y')} МСК"
        blocks = [
            section("🗂 Источник", source_note),
            section(
                "📊 Событий",
                f"Сырых: {summary.get('raw_total_events', summary['total_events'])}",
                f"Шагов: {summary.get('analytics_total_events', summary['total_events'])}",
                f"Клиентов: {summary['unique_users']}",
            ),
        ]
        if reset_note:
            blocks.append(section("🧹 Сброс БД", reset_note))

        top_rows = []
        for row in by_path[:8]:
            mark = "⚠️" if row["fail_rate_pct"] > 10 else "✅"
            top_rows.append(f"{mark} {row['label']}: {row['total']}")
            top_rows.append(f"ок {row['success']}, ошибок {row['fail']} ({row['fail_rate_pct']}%)")
        blocks.append(section("🔥 Топ шагов", *top_rows))

        totals_by_step = {row["step"]: row for row in by_path}
        step_rows = []
        for step in KEY_AUDIT_STEPS_ORDER:
            row = totals_by_step.get(step)
            label = AUDIT_STEP_LABELS.get(step, step)
            step_rows.append(f"{label}: {row['total'] if row else 0}")
        blocks.append(section("🧭 Ключевые шаги", *step_rows))

        success_by_step = _audit_success_event_counts(by_path)
        pay_start = success_by_step.get("pay_start", 0)
        pay_ok = success_by_step.get("pay", 0)
        key_created = success_by_step.get("key_create", 0)
        connect_opened = success_by_step.get("connect", 0)
        pct_pay = round(100.0 * pay_ok / pay_start, 1) if pay_start else 0
        pct_connect = round(100.0 * connect_opened / key_created, 1) if key_created else 0
        blocks.append(section("💳 Оплата", f"Начато: {pay_start}", f"Оплачено: {pay_ok}", f"Доходит: {pct_pay}%"))
        blocks.append(
            section(
                "🔐 Подписка",
                f"Оформлено: {key_created}",
                f"Подключено: {connect_opened}",
                f"Доходит: {pct_connect}%",
            )
        )

        funnel_rows = []
        for step in funnel:
            conv = f" → {step['conversion_from_prev_pct']}%" if step["conversion_from_prev_pct"] is not None else ""
            funnel_rows.append(f"{step['label']}: {step['count']}{conv}")
        blocks.append(section("🪜 Воронка", *funnel_rows))

        return (card(*blocks), None)
    except Exception as e:
        logger.exception("Ошибка при получении аудита: {}", e)
        return (None, str(e))


@router.message(F.text.in_(["Аудит", "аудит"]), IsAdminFilter())
async def handle_audit_command(message: Message, session: AsyncSession):
    """По команде «Аудит» — предложить выбрать источник данных для отчёта."""
    help_text = menu_text(
        "Аудит",
        "Выберите источник данных.",
        quote(
            "<b>Redis raw</b> — сырые последние события из буфера, без добора успешных оплат из базы.",
            "<b>БД вчера</b> — сводка из базы за прошлые сутки, с 00:00 до 00:00 по Москве.",
        ),
        quote("Сброс Redis чистит кэш аудита. Сброс базы применяется отдельно и только при явном выборе в режиме БД."),
    )
    await message.answer(help_text, reply_markup=build_audit_source_kb())


@router.callback_query(AdminPanelCallback.filter(F.action == "audit_refresh"), IsAdminFilter())
async def handle_audit_refresh(callback_query: CallbackQuery, session: AsyncSession):
    """Совместимость со старыми сообщениями аудита: открыть DB-отчёт за прошлые сутки."""
    await callback_query.answer()
    source = "db"
    text, err = await _build_audit_report(session, source=source)
    if err:
        await callback_query.message.edit_text(menu_text("Статистика", f"❌ Ошибка: {escape(err)}"))
        return
    try:
        await callback_query.message.edit_text(
            menu_text("Статистика", text, markup=build_audit_refresh_kb(source)),
            reply_markup=build_audit_refresh_kb(source),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(AdminPanelCallback.filter(F.action == "audit_refresh_redis"), IsAdminFilter())
async def handle_audit_refresh_redis(callback_query: CallbackQuery, session: AsyncSession):
    """Показать сырые данные аудита из Redis."""
    await callback_query.answer()
    source = "redis"
    text, err = await _build_audit_report(session, source=source)
    if err:
        await callback_query.message.edit_text(
            menu_text("Статистика", f"❌ Ошибка: {escape(err)}", markup=build_audit_refresh_kb(source)),
            reply_markup=build_audit_refresh_kb(source),
        )
        return
    try:
        await callback_query.message.edit_text(
            menu_text("Статистика", text, markup=build_audit_refresh_kb(source)),
            reply_markup=build_audit_refresh_kb(source),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(AdminPanelCallback.filter(F.action == "audit_refresh_db"), IsAdminFilter())
async def handle_audit_refresh_db(callback_query: CallbackQuery, session: AsyncSession):
    """Показать аудит из БД за прошлые московские сутки."""
    await callback_query.answer()
    source = "db"
    text, err = await _build_audit_report(session, source=source)
    if err:
        await callback_query.message.edit_text(
            menu_text("Статистика", f"❌ Ошибка: {escape(err)}", markup=build_audit_refresh_kb(source)),
            reply_markup=build_audit_refresh_kb(source),
        )
        return
    try:
        await callback_query.message.edit_text(
            menu_text("Статистика", text, markup=build_audit_refresh_kb(source)),
            reply_markup=build_audit_refresh_kb(source),
        )
    except TelegramBadRequest:
        pass


@router.callback_query(
    AdminPanelCallback.filter(F.action.in_(["audit_reset_ask_redis", "audit_reset_ask_db"])), IsAdminFilter()
)
async def handle_audit_reset_ask(callback_query: CallbackQuery):
    await callback_query.answer()
    source = "redis" if callback_query.data and "redis" in callback_query.data else "db"
    source_label = "Redis raw" if source == "redis" else "БД вчера"
    text = menu_text(
        "Сброс аудита",
        "Подтвердите действие.",
        section("🗂 Источник", source_label),
        quote(
            "Очистится только Redis-буфер отчёта. История по клиентам останется."
            if source == "redis"
            else "В базу запишется отметка сброса, она применится к отчёту из БД."
        ),
    )
    try:
        await callback_query.message.edit_text(text, reply_markup=build_audit_reset_confirm_kb(source))
    except TelegramBadRequest:
        pass


@router.callback_query(
    AdminPanelCallback.filter(F.action.in_(["audit_reset_do_redis", "audit_reset_do_db"])),
    IsAdminFilter(),
    flags={"popup": True},
)
async def handle_audit_reset_do(callback_query: CallbackQuery, session: AsyncSession):
    source = "redis" if callback_query.data and "redis" in callback_query.data else "db"
    try:
        if source == "redis":
            await clear_audit_redis_buffers()
        else:
            await set_audit_db_reset_at(session)
        await callback_query.answer(menu_text("Статистика", "Аудит сброшен"))
        text, err = await _build_audit_report(session, source=source)
        if err:
            await callback_query.message.edit_text(
                menu_text("Статистика", f"❌ Ошибка: {escape(err)}", markup=build_audit_refresh_kb(source)),
                reply_markup=build_audit_refresh_kb(source),
            )
            return
        try:
            await callback_query.message.edit_text(
                menu_text("Статистика", text, markup=build_audit_refresh_kb(source)),
                reply_markup=build_audit_refresh_kb(source),
            )
        except TelegramBadRequest:
            pass
    except Exception as e:
        logger.exception("Ошибка при сбросе аудита ({}): {}", source, e)
        await callback_query.answer("Не удалось сбросить аудит", show_alert=True)


@router.callback_query(AdminPanelCallback.filter(F.action == "stats_export_users_csv"), IsAdminFilter())
async def handle_export_users_csv(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_admin_back_kb("stats")
    try:
        export = await export_users_csv(session)
        await callback_query.message.answer_document(document=export, caption="📅 Экспорт пользователей в CSV")
    except Exception as e:
        logger.error(f"Ошибка при экспорте пользователей: {e}")
        await callback_query.message.edit_text(
            text=menu_text("Статистика", f"❌ Ошибка: {e}", markup=kb), reply_markup=kb
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "stats_export_payments_csv"), IsAdminFilter())
async def handle_export_payments_csv(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_admin_back_kb("stats")
    try:
        export = await export_payments_csv(session)
        await callback_query.message.answer_document(document=export, caption="📅 Экспорт платежей в CSV")
    except Exception as e:
        logger.error(f"Ошибка при экспорте платежей: {e}")
        await callback_query.message.edit_text(
            text=menu_text("Статистика", f"❌ Ошибка: {e}", markup=kb), reply_markup=kb
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "stats_export_hot_leads_csv"), IsAdminFilter())
async def handle_export_hot_leads_csv(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_admin_back_kb("stats")
    try:
        export = await export_hot_leads_csv(session)
        await callback_query.message.answer_document(document=export, caption="📅 Экспорт горящих лидов")
    except Exception as e:
        logger.error(f"Ошибка при экспорте горящих лидов: {e}")
        await callback_query.message.edit_text(
            text=menu_text("Статистика", f"❌ Ошибка: {e}", markup=kb), reply_markup=kb
        )


@router.callback_query(AdminPanelCallback.filter(F.action == "stats_export_keys_csv"), IsAdminFilter())
async def handle_export_keys_csv(callback_query: CallbackQuery, session: AsyncSession):
    kb = build_admin_back_kb("stats")
    try:
        export = await export_keys_csv(session)
        await callback_query.message.answer_document(document=export, caption="📅 Экспорт подписок в CSV")
    except Exception as e:
        logger.error(f"Ошибка при экспорте подписок: {e}")
        await callback_query.message.edit_text(
            text=menu_text("Статистика", f"❌ Ошибка: {e}", markup=kb), reply_markup=kb
        )


def _moscow_day_window(report_date: date, moscow_tz) -> tuple[datetime, datetime]:
    start = moscow_tz.localize(datetime.combine(report_date, datetime.min.time()))
    end = start + timedelta(days=1)
    return start, end


def _fmt_num(value: float) -> str:
    return f"{round(float(value)):,}".replace(",", " ")


def _format_trend(current: float, previous: float, suffix: str = "", label: str = "") -> str:
    """Возвращает изменение к прошлому периоду одной короткой пометкой."""
    diff = round(current - previous, 2)
    if diff == 0:
        return ""
    sign = "+" if diff > 0 else "−"
    return f" ({sign}{_fmt_num(abs(diff))}{suffix}{label})"


async def _day_users_revenue(session, moscow_tz, day: date) -> tuple[int, float]:
    start, end = _moscow_day_window(day, moscow_tz)
    users = await count_users_registered_between(
        session,
        start.astimezone(pytz.UTC).replace(tzinfo=None),
        end.astimezone(pytz.UTC).replace(tzinfo=None),
    )
    revenue = await sum_payments_between(session, start.replace(tzinfo=None), end.replace(tzinfo=None))
    return users, revenue


async def _collect_daily_series(session, moscow_tz, last_day: date, days: int):
    labels: list[str] = []
    users: list[int] = []
    revenue: list[float] = []
    for offset in range(days - 1, -1, -1):
        day = last_day - timedelta(days=offset)
        u, r = await _day_users_revenue(session, moscow_tz, day)
        labels.append(day.strftime("%d"))
        users.append(u)
        revenue.append(r)
    return labels, users, revenue


async def _send_report_to_admins(session: AsyncSession, text: str, chart) -> None:
    from database.models import Admin

    moderator_ids = set((await session.execute(select(Admin.tg_id).where(Admin.role == "moderator"))).scalars().all())
    photo_bytes = chart.getvalue() if chart is not None else None
    for admin_id in ADMIN_ID:
        if admin_id in moderator_ids:
            continue
        try:
            if photo_bytes is not None:
                await bot.send_photo(
                    admin_id,
                    photo=BufferedInputFile(photo_bytes, filename="stats.png"),
                    caption=text,
                )
            else:
                await bot.send_message(admin_id, text)
        except Exception as e:
            logger.warning(f"[Stats] Не удалось отправить отчёт admin={admin_id}: {e}")


async def send_daily_stats_report(session: AsyncSession):
    try:
        from handlers.admin.stats.report_charts import render_stats_chart

        moscow_tz = pytz.timezone("Europe/Moscow")
        now_moscow = datetime.now(moscow_tz)
        update_time = now_moscow.strftime("%d.%m.%Y %H:%M")

        report_date = now_moscow.date() - timedelta(days=1)
        prev_date = report_date - timedelta(days=1)

        from database.statistics import count_payments_between
        from database.subscription_events import get_subscription_dynamics

        new_users, revenue = await _day_users_revenue(session, moscow_tz, report_date)
        new_users_prev, revenue_prev = await _day_users_revenue(session, moscow_tz, prev_date)

        day_start, day_end = _moscow_day_window(report_date, moscow_tz)
        pay_count = await count_payments_between(session, day_start.replace(tzinfo=None), day_end.replace(tzinfo=None))
        avg_check = revenue / pay_count if pay_count else 0

        dyn = await get_subscription_dynamics(session, 15)
        day_key = report_date.strftime("%Y-%m-%d")
        day_ev = next((e for e in dyn.get("dailyEvents", []) if e.get("date") == day_key), {})
        created = int(day_ev.get("created", 0))
        renewed = int(day_ev.get("renewed", 0))
        expired = int(day_ev.get("expired", 0))
        net = created - expired
        net_str = f"{'+' if net >= 0 else '−'}{abs(net)}"

        active_total = await count_active_keys(session)
        total_users = await count_total_users(session)

        labels, users_series, revenue_series = await _collect_daily_series(session, moscow_tz, report_date, 14)
        panels = [
            {"name": "Доход, руб/день", "color": (63, 185, 80), "values": revenue_series},
            {
                "name": "Новые пользователи/день",
                "color": (88, 166, 255),
                "values": [float(v) for v in users_series],
            },
        ]
        chart = render_stats_chart(labels, panels)

        n = len(revenue_series) or 1
        avg_users = round(sum(users_series) / n, 1)
        avg_revenue = sum(revenue_series) / n
        best_idx = max(range(len(revenue_series)), key=lambda i: revenue_series[i]) if revenue_series else 0
        best_label = labels[best_idx] if labels else "—"
        best_revenue = revenue_series[best_idx] if revenue_series else 0
        week_users = int(sum(users_series[-7:]))
        week_revenue = sum(revenue_series[-7:])

        text = menu_text(
            "Сводка за день",
            f"{report_date.strftime('%d.%m.%Y')}",
            card(
                section(
                    "💰 Деньги",
                    f"Доход: {_fmt_num(revenue)} ₽{_format_trend(revenue, revenue_prev)}",
                    f"Платежей: {pay_count}",
                    f"Средний чек: {_fmt_num(avg_check)} ₽",
                ),
                section(
                    "👤 Клиенты",
                    f"Новых: {new_users}{_format_trend(new_users, new_users_prev)}",
                    f"Всего: {_fmt_num(total_users)}",
                ),
                section(
                    "📦 Подписки",
                    f"Новые: {created}",
                    f"Продления: {renewed}",
                    f"Отток: {expired}",
                    f"Прирост: {net_str}",
                    f"Активных: {_fmt_num(active_total)}",
                ),
                section(
                    "📊 За 14 дней",
                    f"В день: {avg_users} и {_fmt_num(avg_revenue)} ₽",
                    f"Лучший: {best_label} — {_fmt_num(best_revenue)} ₽",
                    f"Неделя: {_fmt_num(week_users)} и {_fmt_num(week_revenue)} ₽",
                ),
                section(
                    "🔮 Прогноз",
                    f"Неделя: {_fmt_num(revenue * 7)} ₽",
                    f"Месяц: {_fmt_num(revenue * 30)} ₽",
                ),
                section("⏱ Собрано", f"{update_time} МСК"),
            ),
        )

        await _send_report_to_admins(session, text, chart)

    except Exception as e:
        logger.error(f"[Stats] Ошибка при отправке статистики: {e}")


_MONTH_NAMES_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


def _month_window(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


async def send_monthly_stats_report(session: AsyncSession):
    try:
        from handlers.admin.stats.report_charts import render_stats_chart

        moscow_tz = pytz.timezone("Europe/Moscow")
        now_moscow = datetime.now(moscow_tz)
        update_time = now_moscow.strftime("%d.%m.%Y %H:%M")

        this_month_first = now_moscow.date().replace(day=1)
        last_day = this_month_first - timedelta(days=1)
        month_start, month_end = _month_window(last_day.year, last_day.month)
        days_in_month = (month_end - month_start).days

        prev_last_day = month_start - timedelta(days=1)
        prev_start, prev_end = _month_window(prev_last_day.year, prev_last_day.month)

        def _to_utc_naive(d: date) -> datetime:
            return (
                moscow_tz.localize(datetime.combine(d, datetime.min.time())).astimezone(pytz.UTC).replace(tzinfo=None)
            )

        def _to_local_naive(d: date) -> datetime:
            return moscow_tz.localize(datetime.combine(d, datetime.min.time())).replace(tzinfo=None)

        users_total = await count_users_registered_between(
            session, _to_utc_naive(month_start), _to_utc_naive(month_end)
        )
        users_prev = await count_users_registered_between(session, _to_utc_naive(prev_start), _to_utc_naive(prev_end))
        revenue_total = await sum_payments_between(session, _to_local_naive(month_start), _to_local_naive(month_end))
        revenue_prev = await sum_payments_between(session, _to_local_naive(prev_start), _to_local_naive(prev_end))

        labels, users_series, revenue_series = await _collect_daily_series(session, moscow_tz, last_day, days_in_month)

        panels = [
            {"name": "Доход, руб/день", "color": (63, 185, 80), "values": revenue_series},
            {
                "name": "Новые пользователи/день",
                "color": (88, 166, 255),
                "values": [float(v) for v in users_series],
            },
        ]
        chart = render_stats_chart(labels, panels)

        best_idx = max(range(len(revenue_series)), key=lambda i: revenue_series[i]) if revenue_series else 0
        best_day_label = labels[best_idx] if labels else "—"
        best_day_value = revenue_series[best_idx] if revenue_series else 0.0
        avg_users = round(users_total / days_in_month, 1) if days_in_month else 0
        avg_revenue = round(revenue_total / days_in_month, 1) if days_in_month else 0

        month_title = f"{_MONTH_NAMES_RU.get(last_day.month, '')} {last_day.year}"
        text = menu_text(
            "Отчёт за месяц",
            month_title,
            card(
                section(
                    "👤 Итоги",
                    f"Новых клиентов: {_fmt_num(users_total)}{_format_trend(users_total, users_prev)}",
                    f"Доход: {_fmt_num(revenue_total)} ₽{_format_trend(revenue_total, revenue_prev)}",
                ),
                section(
                    "📈 В среднем",
                    f"Клиентов в день: {avg_users}",
                    f"Дохода в день: {_fmt_num(avg_revenue)} ₽",
                    f"Дней в месяце: {days_in_month}",
                ),
                section("🏆 Лучший день", f"{best_day_label}: {_fmt_num(best_day_value)} ₽"),
                section("⏱ Собрано", f"{update_time} МСК"),
            ),
        )

        await _send_report_to_admins(session, text, chart)

    except Exception as e:
        logger.error(f"[Stats] Ошибка при отправке месячного отчёта: {e}")


@router.message(F.text == "Сводка", IsAdminFilter())
async def test_stats_command(message: Message, session: AsyncSession):
    await send_daily_stats_report(session)
