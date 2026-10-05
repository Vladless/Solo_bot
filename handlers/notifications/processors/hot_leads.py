from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG
from database import add_notification, check_notification_time_bulk, get_hot_leads
from database.access.resolution import notify_telegram_chat_id
from database.notifications import get_hot_lead_notification_times
from database.tariffs import get_tariffs
from handlers.admin.sender.sender_utils import is_telegram_chat_id
from handlers.notifications.keyboards import build_hot_lead_kb
from handlers.notifications.sender import send_notification
from logger import logger
from settings.buttons import MAIN_MENU
from settings.config import DISCOUNT_ACTIVE_HOURS, HOT_LEAD_INTERVAL_HOURS
from settings.texts import HOT_LEAD_FINAL_MESSAGE, HOT_LEAD_LOST_OPPORTUNITY, HOT_LEAD_MESSAGE


async def process_hot_leads(bot: Bot, session: AsyncSession):
    logger.info("[HotLeads] Запуск")

    hot_lead_interval = int(NOTIFICATIONS_CONFIG.get("HOT_LEADS_INTERVAL_HOURS", HOT_LEAD_INTERVAL_HOURS))
    discount_active = int(NOTIFICATIONS_CONFIG.get("DISCOUNT_ACTIVE_HOURS", DISCOUNT_ACTIVE_HOURS))

    try:
        leads = await get_hot_leads(session)
        if not leads:
            return

        expiry_by_user = dict(leads)
        lead_ids = list(expiry_by_user)
        step_times = await get_hot_lead_notification_times(session, lead_ids)
        now = datetime.now(UTC)
        step2_expired_can_send = await check_notification_time_bulk(
            session,
            [(tid, "hot_lead_step_2") for tid in lead_ids],
            discount_active,
        )
        can_send_after_step2 = await check_notification_time_bulk(
            session,
            [(tid, "hot_lead_step_2") for tid in lead_ids],
            hot_lead_interval,
        )

        discount_tariffs = await get_tariffs(session, group_code="discounts")
        active_discounts = [t for t in discount_tariffs if t.get("is_active")]
        discount_max_tariffs = await get_tariffs(session, group_code="discounts_max")
        active_max_discounts = [t for t in discount_max_tariffs if t.get("is_active")]

        notified = 0

        async def _send_to_user(user_id: int, text: str, keyboard) -> bool:
            chat_id = await notify_telegram_chat_id(session, user_id)
            if not is_telegram_chat_id(chat_id):
                return False
            return await send_notification(bot, chat_id, None, text, keyboard)

        for user_id in lead_ids:
            expired_at = expiry_by_user[user_id]
            times = step_times.get(user_id, {})
            done = {step for step, sent_at in times.items() if sent_at is not None and sent_at >= expired_at}

            has_step_2 = "hot_lead_step_2" in done
            has_step_3 = "hot_lead_step_3" in done
            has_expired = "hot_lead_step_2_expired" in done

            if not has_step_2:
                if now - expired_at < timedelta(hours=hot_lead_interval):
                    continue
                if not active_discounts:
                    continue
                keyboard = build_hot_lead_kb()
                if await _send_to_user(user_id, HOT_LEAD_MESSAGE, keyboard):
                    await add_notification(session, user_id, "hot_lead_step_2")
                    notified += 1
                continue

            if not has_step_3 and not has_expired:
                if (user_id, "hot_lead_step_2") in step2_expired_can_send:
                    builder = InlineKeyboardBuilder()
                    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                    if await _send_to_user(user_id, HOT_LEAD_LOST_OPPORTUNITY, builder.as_markup()):
                        await add_notification(session, user_id, "hot_lead_step_2_expired")
                continue

            if not has_step_3:
                if (user_id, "hot_lead_step_2") not in can_send_after_step2:
                    continue
                if not active_max_discounts:
                    continue
                keyboard = build_hot_lead_kb(final=True)
                if await _send_to_user(user_id, HOT_LEAD_FINAL_MESSAGE, keyboard):
                    await add_notification(session, user_id, "hot_lead_step_3")
                    notified += 1

        logger.info(f"[HotLeads] Отправлено: {notified}")

    except Exception as e:
        logger.error(f"[HotLeads] Ошибка: {e}")
