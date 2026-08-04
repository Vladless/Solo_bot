from __future__ import annotations

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG
from database import check_notification_time_bulk, get_cold_lead_notification_flags, get_cold_leads
from database.models import User
from database.notifications import bulk_add_notifications
from handlers.admin.sender.sender_utils import is_telegram_chat_id
from handlers.notifications.keyboards import build_cold_lead_discount_kb
from handlers.notifications.sender import send_messages_with_limit
from logger import logger
from settings.texts import COLD_LEAD_FINAL_MESSAGE, COLD_LEAD_MESSAGE


_DEFAULT_INTERVAL_HOURS = 48
_USER_ID_BATCH_SIZE = 5000
_MESSAGES_PER_SECOND = 30


async def _chat_ids_for_user_ids(session: AsyncSession, user_ids: list[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for i in range(0, len(user_ids), _USER_ID_BATCH_SIZE):
        batch = user_ids[i : i + _USER_ID_BATCH_SIZE]
        result = await session.execute(select(User.id, User.tg_id).where(User.id.in_(batch)))
        for uid, tg_id in result.all():
            if is_telegram_chat_id(tg_id):
                out[int(uid)] = int(tg_id)
    return out


async def _bulk_send(bot: Bot, session: AsyncSession, messages: list[dict]) -> int:
    if not messages:
        return 0
    chat_ids = await _chat_ids_for_user_ids(session, [m["user_id"] for m in messages])
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
