from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG
from database import check_notification_time_bulk, get_cold_lead_notification_flags, get_cold_leads
from database.notifications import bulk_add_notifications
from handlers.notifications.keyboards import build_cold_lead_discount_kb
from handlers.notifications.sender import chat_ids_for_user_ids, send_messages_with_limit
from logger import logger
from settings.texts import COLD_LEAD_FINAL_MESSAGE, COLD_LEAD_MESSAGE


_DEFAULT_INTERVAL_HOURS = 48
_MESSAGES_PER_SECOND = 30


async def _bulk_send(bot: Bot, session: AsyncSession, messages: list[dict]) -> int:
    if not messages:
        return 0
    chat_ids = await chat_ids_for_user_ids(session, [m["user_id"] for m in messages])
    outbound = [
        {"tg_id": chat_ids[m["user_id"]], "text": m["text"], "keyboard": m["keyboard"]}
        for m in messages
        if m["user_id"] in chat_ids
    ]
    if not outbound:
        return 0
    results = await send_messages_with_limit(bot, outbound, messages_per_second=_MESSAGES_PER_SECOND)
    return sum(1 for r in results if r)


async def process_cold_leads(bot: Bot, session: AsyncSession):
    logger.info("[ColdLeads] Запуск")

    interval = int(NOTIFICATIONS_CONFIG.get("COLD_LEADS_INTERVAL_HOURS", _DEFAULT_INTERVAL_HOURS))

    try:
        leads = await get_cold_leads(session)
        if not leads:
            return

        flags = await get_cold_lead_notification_flags(session, leads)
        can_send_step2 = await check_notification_time_bulk(
            session,
            [(tid, "cold_lead_step_1") for tid in leads],
            interval,
        )
        can_send_step3 = await check_notification_time_bulk(
            session,
            [(tid, "cold_lead_step_2") for tid in leads],
            interval,
        )

        step1_to_add: list[int] = []
        step2_messages: list[dict] = []
        step3_messages: list[dict] = []

        for user_id in leads:
            step_flags = flags.get(user_id, set())
            has_step_1 = "cold_lead_step_1" in step_flags
            has_step_2 = "cold_lead_step_2" in step_flags
            has_step_3 = "cold_lead_step_3" in step_flags

            if not has_step_1:
                step1_to_add.append(user_id)
                continue

            if not has_step_2:
                if (user_id, "cold_lead_step_1") not in can_send_step2:
                    continue
                step2_messages.append({
                    "user_id": user_id,
                    "text": COLD_LEAD_MESSAGE,
                    "keyboard": build_cold_lead_discount_kb(),
                })
                continue

            if not has_step_3:
                if (user_id, "cold_lead_step_2") not in can_send_step3:
                    continue
                step3_messages.append({
                    "user_id": user_id,
                    "text": COLD_LEAD_FINAL_MESSAGE,
                    "keyboard": build_cold_lead_discount_kb(final=True),
                })

        if step1_to_add:
            await bulk_add_notifications(
                session,
                [(uid, "cold_lead_step_1") for uid in step1_to_add],
                commit=True,
            )
            logger.info(f"[ColdLeads] Шаг 1 зафиксирован: {len(step1_to_add)}")

        notified = 0

        if step2_messages:
            await bulk_add_notifications(
                session,
                [(m["user_id"], "cold_lead_step_2") for m in step2_messages],
                commit=True,
            )
            notified += await _bulk_send(bot, session, step2_messages)

        if step3_messages:
            await bulk_add_notifications(
                session,
                [(m["user_id"], "cold_lead_step_3") for m in step3_messages],
                commit=True,
            )
            notified += await _bulk_send(bot, session, step3_messages)

        logger.info(f"[ColdLeads] Отправлено: {notified}")

    except Exception as e:
        logger.error(f"[ColdLeads] Ошибка: {e}")
