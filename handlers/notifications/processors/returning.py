from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG
from database.access.resolution import UserId
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
    """Напоминает неактивным клиентам о подписке."""
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

        keyboard = build_cold_lead_kb()
        outbound = [
            {"user_id": uid, "tg_id": chat_ids[uid], "text": RETURNING_MESSAGE, "keyboard": keyboard}
            for uid in deliverable
        ]
        results = await send_messages_with_limit(bot, outbound, messages_per_second=_MESSAGES_PER_SECOND)
        delivered = [UserId(msg["user_id"]) for msg, sent in zip(outbound, results, strict=True) if sent]
        if delivered:
            await bulk_add_notifications(
                session,
                [(uid, RETURNING_NOTIFICATION_TYPE) for uid in delivered],
                commit=True,
            )
        notified = len(delivered)

        logger.info(f"[Returning] Отправлено: {notified}")

    except Exception as e:
        logger.error(f"[Returning] Ошибка: {e}")
