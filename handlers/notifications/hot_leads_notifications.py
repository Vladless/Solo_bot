from aiogram import Bot
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import DISCOUNT_ACTIVE_HOURS, HOT_LEAD_INTERVAL_HOURS
from database import add_notification, check_notification_time, get_hot_leads
from database.models import Notification
from handlers.buttons import MAIN_MENU
from handlers.notifications.notify_kb import build_hot_lead_kb
from handlers.notifications.notify_utils import send_notification
from handlers.texts import (
    HOT_LEAD_FINAL_MESSAGE,
    HOT_LEAD_LOST_OPPORTUNITY,
    HOT_LEAD_MESSAGE,
)
from logger import logger


async def notify_hot_leads(bot: Bot, session: AsyncSession):
    logger.info("Запуск уведомлений для горячих лидов.")

    try:
        leads = await get_hot_leads(session)
        notified = 0

        for tg_id in leads:
            has_step_1 = await session.scalar(
                select(select(Notification).filter_by(tg_id=tg_id, notification_type="hot_lead_step_1").exists())
            )
            if not has_step_1:
                await add_notification(session, tg_id, "hot_lead_step_1")
                logger.info(f"[HOT LEAD] Шаг 1 — зафиксировано без отправки: {tg_id}")
                continue

            has_step_2 = await session.scalar(
                select(select(Notification).filter_by(tg_id=tg_id, notification_type="hot_lead_step_2").exists())
            )
            if not has_step_2:
                can_send = await check_notification_time(
                    session,
                    tg_id=tg_id,
                    notification_type="hot_lead_step_1",
                    hours=HOT_LEAD_INTERVAL_HOURS,
                )
                if not can_send:
                    continue

                keyboard = build_hot_lead_kb()
                result = await send_notification(bot, tg_id, None, HOT_LEAD_MESSAGE, keyboard)
                if result:
                    await add_notification(session, tg_id, "hot_lead_step_2")
                    logger.info(f"Шаг 2 — отправлено первое уведомление: {tg_id}")
                    notified += 1
                continue

            has_step_3 = await session.scalar(
                select(select(Notification).filter_by(tg_id=tg_id, notification_type="hot_lead_step_3").exists())
            )
            has_expired_notification = await session.scalar(
                select(
                    select(Notification).filter_by(tg_id=tg_id, notification_type="hot_lead_step_2_expired").exists()
                )
            )
            if not has_step_3 and not has_expired_notification:
                expired = await check_notification_time(
                    session,
                    tg_id=tg_id,
                    notification_type="hot_lead_step_2",
                    hours=DISCOUNT_ACTIVE_HOURS,
                )
                if expired:
                    builder = InlineKeyboardBuilder()
                    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

                    result = await send_notification(bot, tg_id, None, HOT_LEAD_LOST_OPPORTUNITY, builder.as_markup())
                    if result:
                        await add_notification(session, tg_id, "hot_lead_step_2_expired")
                        logger.info(f"📭 Скидка упущена — отправлено уведомление: {tg_id}")
                    continue

            if not has_step_3:
                can_send = await check_notification_time(
                    session,
                    tg_id=tg_id,
                    notification_type="hot_lead_step_2",
                    hours=HOT_LEAD_INTERVAL_HOURS,
                )
                if not can_send:
                    continue

                keyboard = build_hot_lead_kb(final=True)
                result = await send_notification(bot, tg_id, None, HOT_LEAD_FINAL_MESSAGE, keyboard)
                if result:
                    await add_notification(session, tg_id, "hot_lead_step_3")
                    logger.info(f"⚡ Шаг 3 — отправлено финальное уведомление: {tg_id}")
                    notified += 1

        logger.info(f"Уведомления завершены. Отправлено: {notified}")

    except Exception as e:
        logger.error(f"❌ Ошибка в notify_hot_leads: {e}")
