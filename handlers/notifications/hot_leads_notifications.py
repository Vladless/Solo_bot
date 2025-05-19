import asyncpg
from aiogram import Bot

from config import DATABASE_URL, HOT_LEAD_INTERVAL_HOURS
from database import check_notification_time, add_notification, get_hot_leads
from handlers.notifications.notify_utils import send_notification
from logger import logger
from handlers.notifications.notify_kb import build_hot_lead_kb
from handlers.texts import HOT_LEAD_MESSAGE, HOT_LEAD_FINAL_MESSAGE


async def notify_hot_leads(bot: Bot):
    logger.info("🚀 Запуск уведомлений для горячих лидов.")
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        leads = await get_hot_leads(conn)
        notified = 0

        for row in leads:
            tg_id = row["tg_id"]

            has_step_1 = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM notifications WHERE tg_id = $1 AND notification_type = 'hot_lead_step_1')",
                tg_id
            )
            if not has_step_1:
                await add_notification(tg_id, "hot_lead_step_1", session=conn)
                logger.info(f"[HOT LEAD] Шаг 1 — зафиксировано без отправки: {tg_id}")
                continue

            has_step_2 = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM notifications WHERE tg_id = $1 AND notification_type = 'hot_lead_step_2')",
                tg_id
            )
            if not has_step_2:
                can_send = await check_notification_time(
                    tg_id=tg_id,
                    notification_type="hot_lead_step_1",
                    hours=HOT_LEAD_INTERVAL_HOURS,
                    session=conn
                )
                if not can_send:
                    continue

                keyboard = build_hot_lead_kb()
                result = await send_notification(bot, tg_id, None, HOT_LEAD_MESSAGE, keyboard)
                if result:
                    await add_notification(tg_id, "hot_lead_step_2", session=conn)
                    logger.info(f"🔥 Шаг 2 — отправлено первое уведомление: {tg_id}")
                    notified += 1
                continue

            has_step_3 = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM notifications WHERE tg_id = $1 AND notification_type = 'hot_lead_step_3')",
                tg_id
            )
            if not has_step_3:
                can_send = await check_notification_time(
                    tg_id=tg_id,
                    notification_type="hot_lead_step_2",
                    hours=HOT_LEAD_INTERVAL_HOURS,
                    session=conn
                )
                if not can_send:
                    continue

                keyboard = build_hot_lead_kb(final=True)
                result = await send_notification(bot, tg_id, None, HOT_LEAD_FINAL_MESSAGE, keyboard)
                if result:
                    await add_notification(tg_id, "hot_lead_step_3", session=conn)
                    logger.info(f"⚡ Шаг 3 — отправлено финальное уведомление: {tg_id}")
                    notified += 1

        logger.info(f"✅ Уведомления завершены. Отправлено: {notified}")

    except Exception as e:
        logger.error(f"❌ Ошибка в notify_hot_leads: {e}")
    finally:
        await conn.close()

