from __future__ import annotations

import asyncio

from datetime import datetime

import pytz

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.bootstrap import MODES_CONFIG, NOTIFICATIONS_CONFIG
from handlers.notifications.bulk import create_bulk_updates, execute_bulk_updates
from handlers.notifications.context import NotificationContext
from handlers.notifications.preload import preload_with_fallback
from handlers.notifications.processors.cold_leads import process_cold_leads
from handlers.notifications.processors.expired import process_expired_keys
from handlers.notifications.processors.expiring import process_expiring_keys
from handlers.notifications.processors.hot_leads import process_hot_leads
from handlers.notifications.processors.inactive_trial import process_inactive_trial
from handlers.notifications.processors.returning import process_returning
from handlers.notifications.processors.zero_traffic import process_zero_traffic
from hooks.hooks import run_hooks
from logger import logger
from middlewares.session import wrap_session
from settings.config import (
    NOTIFICATION_TIME,
    NOTIFY_10H_ENABLED,
    NOTIFY_10H_HOURS,
    NOTIFY_24H_ENABLED,
    NOTIFY_24H_HOURS,
    NOTIFY_DELETE_DELAY,
    NOTIFY_DELETE_KEY,
    NOTIFY_HOT_LEADS,
    NOTIFY_INACTIVE_TRAFFIC,
    NOTIFY_RENEW,
    NOTIFY_RENEW_EXPIRED,
    TRIAL_TIME_DISABLE,
)


moscow_tz = pytz.timezone("Europe/Moscow")
notification_lock = asyncio.Lock()


async def periodic_notifications(bot: Bot, *, sessionmaker: async_sessionmaker):
    while True:
        interval = int(NOTIFICATIONS_CONFIG.get("BASE_NOTIFICATION_MINUTE", NOTIFICATION_TIME))

        if notification_lock.locked():
            logger.warning("Уведомления уже выполняются. Пропуск...")
            await asyncio.sleep(interval)
            continue

        async with notification_lock:
            try:
                await _run_cycle(bot, sessionmaker)
            except Exception as error:
                logger.error(f"Ошибка в periodic_notifications: {error}")

        await asyncio.sleep(interval)


async def _run_cycle(bot: Bot, sessionmaker: async_sessionmaker):
    start_time = datetime.now()
    current_time = int(datetime.now(moscow_tz).timestamp() * 1000)

    async with sessionmaker() as preload_session:
        keys, preload_data = await preload_with_fallback(preload_session)

    if not keys and not preload_data:
        logger.info("Нет данных для обработки")
        return

    bulk_updates = create_bulk_updates()

    async with sessionmaker() as session:
        session = wrap_session(session, sessionmaker)
        ctx = NotificationContext(
            bot=bot,
            session=session,
            current_time=current_time,
            preload_data=preload_data,
            bulk_updates=bulk_updates,
        )

        trial_disabled = bool(MODES_CONFIG.get("TRIAL_TIME_DISABLED", TRIAL_TIME_DISABLE))
        notify_24_enabled = bool(NOTIFICATIONS_CONFIG.get("EXPIRY_24H_ENABLED", NOTIFY_24H_ENABLED))
        notify_24_hours = int(NOTIFICATIONS_CONFIG.get("EXPIRY_24H_BEFORE_HOURS", NOTIFY_24H_HOURS))
        notify_10_enabled = bool(NOTIFICATIONS_CONFIG.get("EXPIRY_10H_ENABLED", NOTIFY_10H_ENABLED))
        notify_10_hours = int(NOTIFICATIONS_CONFIG.get("EXPIRY_10H_BEFORE_HOURS", NOTIFY_10H_HOURS))
        renew_enabled = bool(NOTIFICATIONS_CONFIG.get("RENEW_ENABLED", NOTIFY_RENEW))
        renew_expired_enabled = bool(NOTIFICATIONS_CONFIG.get("RENEW_EXPIRED_ENABLED", NOTIFY_RENEW_EXPIRED))
        delete_key_enabled = bool(NOTIFICATIONS_CONFIG.get("DELETE_KEY_ENABLED", NOTIFY_DELETE_KEY))
        delete_delay = int(NOTIFICATIONS_CONFIG.get("DELETE_KEY_DELAY_MINUTES", NOTIFY_DELETE_DELAY))
        inactive_traffic = bool(NOTIFICATIONS_CONFIG.get("INACTIVE_TRAFFIC_ENABLED", NOTIFY_INACTIVE_TRAFFIC))
        hot_leads_enabled = bool(NOTIFICATIONS_CONFIG.get("HOT_LEADS_ENABLED", NOTIFY_HOT_LEADS))
        cold_leads_enabled = bool(NOTIFICATIONS_CONFIG.get("COLD_LEADS_ENABLED", False))
        returning_enabled = bool(NOTIFICATIONS_CONFIG.get("RETURNING_ENABLED", False))

        if not trial_disabled:
            try:
                await process_inactive_trial(bot, session, sessionmaker=sessionmaker)
            except Exception as e:
                logger.error(f"Ошибка inactive_trial: {e}")

        if notify_24_enabled:
            try:
                await process_expiring_keys(
                    ctx,
                    keys,
                    min_hours=notify_10_hours if notify_10_enabled else 0,
                    max_hours=notify_24_hours,
                    notify_type="key_24h",
                    photo="notify_24h.jpg",
                    notify_renew_enabled=renew_enabled,
                    sessionmaker=sessionmaker,
                )
            except Exception as e:
                logger.error(f"Ошибка expiring 24h: {e}")

        if notify_10_enabled:
            try:
                await process_expiring_keys(
                    ctx,
                    keys,
                    min_hours=0,
                    max_hours=notify_10_hours,
                    notify_type="key_10h",
                    photo="notify_10h.jpg",
                    notify_renew_enabled=renew_enabled,
                    sessionmaker=sessionmaker,
                )
            except Exception as e:
                logger.error(f"Ошибка expiring 10h: {e}")

        try:
            await process_expired_keys(
                ctx,
                keys,
                notify_renew_expired=renew_expired_enabled,
                notify_delete_key=delete_key_enabled,
                delete_delay_minutes=delete_delay,
            )
        except Exception as e:
            logger.error(f"Ошибка expired: {e}")

        if inactive_traffic:
            try:
                await process_zero_traffic(bot, session, current_time, keys)
            except Exception as e:
                logger.error(f"Ошибка zero_traffic: {e}")

        try:
            await run_hooks("periodic_notifications", bot=bot, session=session, keys=keys)
        except Exception as e:
            logger.error(f"Ошибка хуков: {e}")

        if hot_leads_enabled:
            try:
                await process_hot_leads(bot, session)
            except Exception as e:
                logger.error(f"Ошибка hot_leads: {e}")

        if cold_leads_enabled:
            try:
                await process_cold_leads(bot, session)
            except Exception as e:
                logger.error(f"Ошибка cold_leads: {e}")

        if returning_enabled:
            try:
                await process_returning(bot, session)
            except Exception as e:
                logger.error(f"Ошибка returning: {e}")

        if bulk_updates:
            bulk_start = datetime.now()
            await execute_bulk_updates(session, bulk_updates)
            bulk_time = (datetime.now() - bulk_start).total_seconds()
            logger.info(f"Bulk за {bulk_time:.2f}s")

        total_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"Уведомления завершены за {total_time:.2f}s")
