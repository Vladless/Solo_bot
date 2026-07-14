from core.tasks.cron_tasks import (
    ABANDONED_CHECKOUT_TRIGGER,
    ANOMALY_CHECK_TRIGGER,
    AUDIT_DRAIN_TRIGGER,
    AUTOCLOSE_TICKETS_TRIGGER,
    DAILY_STATS_REPORT_TRIGGER,
    DB_POOL_STATUS_TRIGGER,
    EXPIRED_GIFTS_CLEANUP_TRIGGER,
    KEY_TRAFFIC_HOURLY_SNAPSHOT_TRIGGER,
    KEY_TRAFFIC_SNAPSHOT_TRIGGER,
    MONTHLY_STATS_REPORT_TRIGGER,
    STALE_PAYMENTS_SWEEP_TRIGGER,
    SUBSCRIPTION_METRICS_SNAPSHOT_TRIGGER,
    WEB_ANALYTICS_CLEANUP_TRIGGER,
    abandoned_checkout_reminder_job,
    abandoned_checkout_reminder_process_runner,
    anomaly_check_job,
    anomaly_check_process_runner,
    autoclose_stale_tickets_job,
    autoclose_stale_tickets_process_runner,
    cleanup_expired_gifts_job,
    cleanup_expired_gifts_process_runner,
    cleanup_web_analytics_job,
    cleanup_web_analytics_process_runner,
    log_db_pool_status,
    scheduled_audit_drain,
    scheduled_audit_drain_process_runner,
    scheduled_monthly_stats_report,
    scheduled_stats_report,
    scheduled_stats_report_process_runner,
    snapshot_key_traffic_hourly_job,
    snapshot_key_traffic_hourly_process_runner,
    snapshot_key_traffic_job,
    snapshot_key_traffic_process_runner,
    snapshot_subscription_metrics_job,
    snapshot_subscription_metrics_process_runner,
    sweep_stale_payments_job,
    sweep_stale_payments_process_runner,
)
from core.tasks.loop_tasks import (
    backup_loop,
    backup_thread_loop,
    blocked_drain_loop,
    notifications_loop,
    remnawave_monitor_loop,
    scheduled_broadcasts_loop_task,
    server_checks_loop,
)
from core.tasks.periodic_manager import periodic_task_manager


_TASKS_REGISTERED = False


def register_periodic_tasks() -> None:
    global _TASKS_REGISTERED
    if _TASKS_REGISTERED:
        return
    from config import PROCESS_POOL_SIZE

    process_budget = max(0, int(PROCESS_POOL_SIZE) if int(PROCESS_POOL_SIZE) > 1 else 0)

    if process_budget > 0:
        periodic_task_manager.register_process_loop_task("notifications", notifications_loop)
        process_budget -= 1
    else:
        periodic_task_manager.register_loop_task("notifications", notifications_loop)

    if process_budget > 0:
        periodic_task_manager.register_process_loop_task("scheduled_broadcasts", scheduled_broadcasts_loop_task)
        process_budget -= 1
    else:
        periodic_task_manager.register_loop_task("scheduled_broadcasts", scheduled_broadcasts_loop_task)

    if process_budget > 0:
        periodic_task_manager.register_process_loop_task("backup", backup_loop)
        process_budget -= 1
    else:
        periodic_task_manager.register_thread_loop_task("backup", backup_thread_loop)

    periodic_task_manager.register_loop_task("blocked_drain", blocked_drain_loop)

    if process_budget > 0:
        periodic_task_manager.register_process_loop_task("server_checks", server_checks_loop)
        process_budget -= 1
    else:
        periodic_task_manager.register_loop_task("server_checks", server_checks_loop)

    periodic_task_manager.register_loop_task("remnawave_monitor", remnawave_monitor_loop)

    periodic_task_manager.set_scheduler_process_workers(process_budget)

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "audit_drain_midnight",
            scheduled_audit_drain_process_runner,
            AUDIT_DRAIN_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "audit_drain_midnight",
            scheduled_audit_drain,
            AUDIT_DRAIN_TRIGGER,
        )

    periodic_task_manager.register_cron_task(
        "daily_stats_report",
        scheduled_stats_report,
        DAILY_STATS_REPORT_TRIGGER,
    )

    periodic_task_manager.register_cron_task(
        "monthly_stats_report",
        scheduled_monthly_stats_report,
        MONTHLY_STATS_REPORT_TRIGGER,
    )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "sweep_stale_payments",
            sweep_stale_payments_process_runner,
            STALE_PAYMENTS_SWEEP_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "sweep_stale_payments",
            sweep_stale_payments_job,
            STALE_PAYMENTS_SWEEP_TRIGGER,
        )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "cleanup_expired_gifts",
            cleanup_expired_gifts_process_runner,
            EXPIRED_GIFTS_CLEANUP_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "cleanup_expired_gifts",
            cleanup_expired_gifts_job,
            EXPIRED_GIFTS_CLEANUP_TRIGGER,
        )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "autoclose_stale_tickets",
            autoclose_stale_tickets_process_runner,
            AUTOCLOSE_TICKETS_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "autoclose_stale_tickets",
            autoclose_stale_tickets_job,
            AUTOCLOSE_TICKETS_TRIGGER,
        )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "cleanup_web_analytics",
            cleanup_web_analytics_process_runner,
            WEB_ANALYTICS_CLEANUP_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "cleanup_web_analytics",
            cleanup_web_analytics_job,
            WEB_ANALYTICS_CLEANUP_TRIGGER,
        )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "abandoned_checkout_reminder",
            abandoned_checkout_reminder_process_runner,
            ABANDONED_CHECKOUT_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "abandoned_checkout_reminder",
            abandoned_checkout_reminder_job,
            ABANDONED_CHECKOUT_TRIGGER,
        )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "snapshot_key_traffic",
            snapshot_key_traffic_process_runner,
            KEY_TRAFFIC_SNAPSHOT_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "snapshot_key_traffic",
            snapshot_key_traffic_job,
            KEY_TRAFFIC_SNAPSHOT_TRIGGER,
        )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "snapshot_key_traffic_hourly",
            snapshot_key_traffic_hourly_process_runner,
            KEY_TRAFFIC_HOURLY_SNAPSHOT_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "snapshot_key_traffic_hourly",
            snapshot_key_traffic_hourly_job,
            KEY_TRAFFIC_HOURLY_SNAPSHOT_TRIGGER,
        )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "snapshot_subscription_metrics",
            snapshot_subscription_metrics_process_runner,
            SUBSCRIPTION_METRICS_SNAPSHOT_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "snapshot_subscription_metrics",
            snapshot_subscription_metrics_job,
            SUBSCRIPTION_METRICS_SNAPSHOT_TRIGGER,
        )

    if process_budget > 0:
        periodic_task_manager.register_cron_task(
            "anomaly_check",
            anomaly_check_process_runner,
            ANOMALY_CHECK_TRIGGER,
            execution_mode="process",
        )
    else:
        periodic_task_manager.register_cron_task(
            "anomaly_check",
            anomaly_check_job,
            ANOMALY_CHECK_TRIGGER,
        )

    periodic_task_manager.register_cron_task(
        "db_pool_status",
        log_db_pool_status,
        DB_POOL_STATUS_TRIGGER,
    )

    _TASKS_REGISTERED = True
