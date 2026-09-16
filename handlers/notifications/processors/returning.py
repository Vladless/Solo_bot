from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG
from database.notifications import bulk_add_notifications
from database.returning import RETURNING_NOTIFICATION_TYPE, get_returning_targets
from handlers.notifications.keyboards import build_cold_lead_kb
from handlers.notifications.sender import chat_ids_for_user_ids, send_messages_with_limit
from logger import logger
from settings.texts import RETURNING_MESSAGE


_DEFAULT_MIN_DAYS = 60
_DEFAULT_MAX_DAYS = 180
_MESSAGES_PER_SECOND = 30


async def process_returning(bot: Bot, session: AsyncSession):
    """Возврат давно ушедших («второй эшелон» после горячих лидов): тем, кто ушёл
    давно (60–180 дней назад) и не вернулся, — мягкое напоминание без скидки."""
    logger.info("[Returning] Запуск")
    min_days = int(NOTIFICATIONS_CONFIG.get("RETURNING_MIN_DAYS", _DEFAULT_MIN_DAYS))
    max_days = int(NOTIFICATIONS_CONFIG.get("RETURNING_MAX_DAYS", _DEFAULT_MAX_DAYS))

    try:
        targets = await get_returning_targets(session, min_days, max_days)
        if not targets:
            return

        chat_ids = await chat_ids_for_user_ids(session, targets)
        deliverable = [uid for uid in targets if uid in chat_ids]
        if not deliverable:
            return

        await bulk_add_notifications(
            session,
            [(uid, RETURNING_NOTIFICATION_TYPE) for uid in deliverable],
            commit=True,
        )

        keyboard = build_cold_lead_kb()
        outbound = [{"tg_id": chat_ids[uid], "text": RETURNING_MESSAGE, "keyboard": keyboard} for uid in deliverable]
        results = await send_messages_with_limit(bot, outbound, messages_per_second=_MESSAGES_PER_SECOND)
        notified = sum(1 for r in results if r)

        logger.info(f"[Returning] Отправлено: {notified}")

    except Exception as e:
        logger.error(f"[Returning] Ошибка: {e}")
