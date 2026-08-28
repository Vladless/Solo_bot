import asyncio

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from database import async_session_maker, cancel_expired_pending_payments
from logger import logger


async def scheduled_audit_drain() -> None:
    from audit import drain_audit_redis_to_db

    try:
        drained = await drain_audit_redis_to_db(async_session_maker)
        logger.info("[AuditDrain] Ночной drain завершён, записано событий: {}", drained)
    except Exception as error:
        logger.error("[AuditDrain] Ошибка ночного drain: {}", error)


async def scheduled_stats_report() -> None:
    from handlers.admin.stats.stats_handler import send_daily_stats_report

    async with async_session_maker() as session:
        await send_daily_stats_report(session)


async def scheduled_monthly_stats_report() -> None:
    from handlers.admin.stats.stats_handler import send_monthly_stats_report

    async with async_session_maker() as session:
        await send_monthly_stats_report(session)


async def sweep_stale_payments_job() -> None:
    async with async_session_maker() as session:
        await cancel_expired_pending_payments(session)
        await session.commit()


def scheduled_audit_drain_process_runner() -> None:
    asyncio.run(scheduled_audit_drain())


def scheduled_stats_report_process_runner() -> None:
    asyncio.run(scheduled_stats_report())


def sweep_stale_payments_process_runner() -> None:
    asyncio.run(sweep_stale_payments_job())


async def cleanup_expired_gifts_job() -> None:
    from datetime import datetime

    from sqlalchemy import update as sa_update

    from database.models import Gift

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                sa_update(Gift).where(Gift.expiry_time < datetime.utcnow(), Gift.is_used is False).values(is_used=True)
            )
            count = result.rowcount
            await session.commit()
            if count:
                logger.info("[GiftCleanup] Просроченных подарков помечено использованными: {}", count)
        except Exception as error:
            logger.error("[GiftCleanup] Ошибка очистки подарков: {}", error)


def cleanup_expired_gifts_process_runner() -> None:
    asyncio.run(cleanup_expired_gifts_job())


async def autoclose_stale_tickets_job() -> None:
    from services.tickets import delete_stale

    async with async_session_maker() as session:
        try:
            count = await delete_stale(session, answered_days=2, closed_days=1)
            await session.commit()
            if count:
                logger.info("[TicketsCleanup] Удалено протухших обращений (с темами): {}", count)
        except Exception as error:
            logger.error("[TicketsCleanup] Ошибка очистки обращений: {}", error)


def autoclose_stale_tickets_process_runner() -> None:
    asyncio.run(autoclose_stale_tickets_job())


WEB_ANALYTICS_RETENTION_DAYS = 90
WEB_ERROR_RETENTION_DAYS = 30
# Журнал доступа пишется на каждый запрос к API, включая внешние сканы.
# Удаление для него было написано, но не вызывалось — таблица росла без предела.
AUDIT_RETENTION_DAYS = 90


async def cleanup_web_analytics_job() -> None:
    """Удаляет старую веб-аналитику, чтобы таблицы не росли бесконечно."""
    from datetime import (
        datetime,
        timedelta,
        timezone as _tz,
    )

    from sqlalchemy import delete as sa_delete

    from database.models import KeyTrafficHistory
    from database.models.web import WebErrorReport, WebFlowEvent, WebPageView

    now = datetime.now(_tz.utc)
    analytics_cutoff = now - timedelta(days=WEB_ANALYTICS_RETENTION_DAYS)
    error_cutoff = now - timedelta(days=WEB_ERROR_RETENTION_DAYS)
    traffic_cutoff = (now - timedelta(days=180)).date()

    async with async_session_maker() as session:
        try:
            pv = await session.execute(sa_delete(WebPageView).where(WebPageView.created_at < analytics_cutoff))
            fe = await session.execute(sa_delete(WebFlowEvent).where(WebFlowEvent.created_at < analytics_cutoff))
            er = await session.execute(
                sa_delete(WebErrorReport).where(
                    WebErrorReport.resolved.is_(True),
                    WebErrorReport.last_seen_at < error_cutoff,
                )
            )
            th = await session.execute(
                sa_delete(KeyTrafficHistory).where(KeyTrafficHistory.snapshot_date < traffic_cutoff)
            )
            from sqlalchemy import text as _sa_text

            rl_cutoff = int(now.timestamp()) - 3600
            rl = await session.execute(
                _sa_text("DELETE FROM rate_limit_counters WHERE window_start < :c"), {"c": rl_cutoff}
            )

            from audit import delete_old_audit_events

            audit_removed = await delete_old_audit_events(session, older_than_days=AUDIT_RETENTION_DAYS)
            await session.commit()
            logger.info(
                "[WebAnalyticsCleanup] удалено page_views={} flow_events={} error_reports={} "
                "traffic_history={} rate_limit={} audit_events={}",
                pv.rowcount,
                fe.rowcount,
                er.rowcount,
                th.rowcount,
                rl.rowcount,
                audit_removed,
            )
        except Exception as error:
            logger.error("[WebAnalyticsCleanup] ошибка очистки: {}", error)


def cleanup_web_analytics_process_runner() -> None:
    asyncio.run(cleanup_web_analytics_job())


async def abandoned_checkout_reminder_job() -> None:
    """Напоминает пользователям о незавершённой оплате (брошенный checkout)."""
    from services.abandoned_checkout import send_abandoned_checkout_reminders

    async with async_session_maker() as session:
        try:
            count = await send_abandoned_checkout_reminders(session)
            await session.commit()
            if count:
                logger.info("[AbandonedCheckout] Напоминаний отправлено: {}", count)
        except Exception as error:
            logger.error("[AbandonedCheckout] Ошибка отправки напоминаний: {}", error)


def abandoned_checkout_reminder_process_runner() -> None:
    asyncio.run(abandoned_checkout_reminder_job())


async def snapshot_key_traffic_job() -> None:
    """Дневной снапшот использованного трафика по ключам (для графиков использования)."""
    from services.traffic_history import snapshot_all_key_traffic

    async with async_session_maker() as session:
        try:
            count = await snapshot_all_key_traffic(session)
            await session.commit()
            if count:
                logger.info("[TrafficHistory] Снапшотов трафика записано: {}", count)
        except Exception as error:
            logger.error("[TrafficHistory] Ошибка снапшота трафика: {}", error)


def snapshot_key_traffic_process_runner() -> None:
    asyncio.run(snapshot_key_traffic_job())


async def snapshot_key_traffic_hourly_job() -> None:
    """Почасовой снапшот использованного трафика (для графика использования за сутки)."""
    from services.traffic_history import snapshot_all_key_traffic_hourly

    async with async_session_maker() as session:
        try:
            count = await snapshot_all_key_traffic_hourly(session)
            await session.commit()
            if count:
                logger.info("[TrafficHistory] Почасовых снапшотов записано: {}", count)
        except Exception as error:
            logger.error("[TrafficHistory] Ошибка почасового снапшота: {}", error)


def snapshot_key_traffic_hourly_process_runner() -> None:
    asyncio.run(snapshot_key_traffic_hourly_job())


async def snapshot_subscription_metrics_job() -> None:
    """Дневной снапшот подписок (активные/отток/тарифы) + одноразовый backfill из платежей."""
    from database.subscription_events import backfill_from_payments, snapshot_daily_metrics

    async with async_session_maker() as session:
        try:
            seeded = await backfill_from_payments(session)
            await snapshot_daily_metrics(session)
            await session.commit()
            if seeded:
                logger.info("[SubMetrics] backfill из платежей: записано {} событий", seeded)
        except Exception as error:
            logger.error("[SubMetrics] Ошибка снапшота подписок: {}", error)


def snapshot_subscription_metrics_process_runner() -> None:
    asyncio.run(snapshot_subscription_metrics_job())


async def anomaly_check_job() -> None:
    """Утренняя проверка аномалий: высокий процент отказов платежей и всплеск оттока."""
    from datetime import datetime, timedelta

    from sqlalchemy import func, select

    from database.models import Payment, SubscriptionEvent

    now = datetime.utcnow()
    y_start = datetime(now.year, now.month, now.day) - timedelta(days=1)
    y_end = y_start + timedelta(days=1)
    alerts: list[str] = []
    async with async_session_maker() as session:
        try:
            row = (
                await session.execute(
                    select(
                        func.count().filter(Payment.status == "success").label("ok"),
                        func.count().filter(Payment.status.in_(["failed", "cancelled"])).label("bad"),
                    )
                    .where(Payment.created_at >= y_start)
                    .where(Payment.created_at < y_end)
                )
            ).first()
            ok = int(row.ok or 0) if row else 0
            bad = int(row.bad or 0) if row else 0
            total = ok + bad
            if total >= 10 and bad / total > 0.3:
                alerts.append(
                    f"⚠️ Высокий процент отказов платежей за вчера: {bad}/{total} ({round(bad / total * 100)}%)."
                )

            exp_y = (
                await session.scalar(
                    select(func.count())
                    .select_from(SubscriptionEvent)
                    .where(SubscriptionEvent.event_type.in_(["expired", "deleted"]))
                    .where(SubscriptionEvent.created_at >= y_start)
                    .where(SubscriptionEvent.created_at < y_end)
                )
            ) or 0
            week_start = y_start - timedelta(days=7)
            exp_week = (
                await session.scalar(
                    select(func.count())
                    .select_from(SubscriptionEvent)
                    .where(SubscriptionEvent.event_type.in_(["expired", "deleted"]))
                    .where(SubscriptionEvent.created_at >= week_start)
                    .where(SubscriptionEvent.created_at < y_start)
                )
            ) or 0
            avg = exp_week / 7.0
            if avg >= 5 and exp_y > avg * 2:
                alerts.append(f"⚠️ Всплеск оттока за вчера: истекло {exp_y} (среднее за неделю ~{round(avg)}).")
        except Exception as error:
            logger.error("[Anomaly] ошибка проверки аномалий: {}", error)
            return

    if alerts:
        try:
            from services.admin_alert import send_admin_alert

            await send_admin_alert("📊 Аналитика — внимание:\n" + "\n".join(alerts))
        except Exception as error:
            logger.error("[Anomaly] не удалось отправить алерт: {}", error)


def anomaly_check_process_runner() -> None:
    asyncio.run(anomaly_check_job())


async def log_db_pool_status() -> None:
    """Раз в минуту логирует состояние пула соединений: даёт видимость «упираемся ли в лимит»."""
    try:
        from database.db import engine

        pool = engine.pool
        size = pool.size()
        checked_out = pool.checkedout()
        checked_in = getattr(pool, "checkedin", lambda: size - checked_out)()
        raw_overflow = getattr(pool, "overflow", lambda: 0)()
        overflow_in_use = max(0, raw_overflow)
        logger.info(
            "[DBPool] пул={} занято={} свободно={} сверх лимита={}",
            size,
            checked_out,
            checked_in,
            overflow_in_use,
        )
    except Exception as error:
        logger.debug("[DBPool] не удалось получить статус пула: {}", error)


AUDIT_DRAIN_TRIGGER = CronTrigger(hour=0, minute=0, timezone="Europe/Moscow")
DAILY_STATS_REPORT_TRIGGER = CronTrigger(hour=0, minute=1, timezone="Europe/Moscow")
MONTHLY_STATS_REPORT_TRIGGER = CronTrigger(day=1, hour=0, minute=10, timezone="Europe/Moscow")
STALE_PAYMENTS_SWEEP_TRIGGER = CronTrigger(minute=0, timezone="Europe/Moscow")
EXPIRED_GIFTS_CLEANUP_TRIGGER = CronTrigger(hour=3, minute=0, timezone="Europe/Moscow")
AUTOCLOSE_TICKETS_TRIGGER = CronTrigger(hour=4, minute=0, timezone="Europe/Moscow")
WEB_ANALYTICS_CLEANUP_TRIGGER = CronTrigger(hour=3, minute=30, timezone="Europe/Moscow")
ABANDONED_CHECKOUT_TRIGGER = CronTrigger(minute=20, timezone="Europe/Moscow")
KEY_TRAFFIC_SNAPSHOT_TRIGGER = CronTrigger(hour=0, minute=10, timezone="Europe/Moscow")
KEY_TRAFFIC_HOURLY_SNAPSHOT_TRIGGER = CronTrigger(minute=5, timezone="Europe/Moscow")
SUBSCRIPTION_METRICS_SNAPSHOT_TRIGGER = CronTrigger(hour=0, minute=20, timezone="Europe/Moscow")
ANOMALY_CHECK_TRIGGER = CronTrigger(hour=9, minute=0, timezone="Europe/Moscow")
DB_POOL_STATUS_TRIGGER = IntervalTrigger(minutes=1)
