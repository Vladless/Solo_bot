from __future__ import annotations

from aiogram import Bot, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.bootstrap import NOTIFICATIONS_CONFIG
from database import check_notifications_bulk
from database.models import User
from database.notifications import bulk_add_notifications
from database.tariffs import get_tariffs
from handlers.notifications.sender import send_messages_with_limit
from handlers.utils import format_days
from logger import logger
from settings.buttons import MAIN_MENU, TRIAL_BONUS
from settings.config import NOTIFY_EXTRA_DAYS, NOTIFY_INACTIVE
from settings.texts import TRIAL_INACTIVE_BONUS_MSG, TRIAL_INACTIVE_FIRST_MSG


_INACTIVE_TRIAL_UPDATE_BATCH_SIZE = 1000
_INACTIVE_TRIAL_NOTIFY_BATCH_SIZE = 1000


async def process_inactive_trial(
    bot: Bot,
    session: AsyncSession,
    *,
    sessionmaker: async_sessionmaker | None = None,
):
    inactive_hours = int(NOTIFICATIONS_CONFIG.get("INACTIVE_USER_ENABLED", NOTIFY_INACTIVE))
    extra_days = int(NOTIFICATIONS_CONFIG.get("EXTRA_DAYS_AFTER_EXPIRY", NOTIFY_EXTRA_DAYS))

    if inactive_hours <= 0:
        return

    users = await check_notifications_bulk(session, "inactive_trial", inactive_hours)
    if not users:
        return

    logger.info(f"[InactiveTrial] {len(users)} неактивных пользователей")

    trial_tariffs = await get_tariffs(session, group_code="trial")
    if not trial_tariffs:
        logger.error("[InactiveTrial] Триальный тариф не найден")
        return

    trial_days = trial_tariffs[0]["duration_days"]
    messages = []
    users_to_extend = []

    for user in users:
        tg_id = user["tg_id"]
        display_name = user["username"] or user["first_name"] or user["last_name"] or "Пользователь"

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text=TRIAL_BONUS, callback_data="create_key"))
        builder.row(types.InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        keyboard = builder.as_markup()

        trial_extended = user["last_notification_time"] is not None

        if trial_extended and extra_days > 0:
            total_days = extra_days + trial_days
            message = TRIAL_INACTIVE_BONUS_MSG.format(
                display_name=display_name,
                extra_days_formatted=format_days(extra_days),
                total_days_formatted=format_days(total_days),
            )
            users_to_extend.append(tg_id)
        else:
            message = TRIAL_INACTIVE_FIRST_MSG.format(
                display_name=display_name,
                trial_time_formatted=format_days(trial_days),
            )

        messages.append({
            "tg_id": tg_id,
            "text": message,
            "keyboard": keyboard,
            "notification_id": "inactive_trial",
        })

    if users_to_extend:
        for i in range(0, len(users_to_extend), _INACTIVE_TRIAL_UPDATE_BATCH_SIZE):
            batch = users_to_extend[i : i + _INACTIVE_TRIAL_UPDATE_BATCH_SIZE]
            await session.execute(update(User).where(User.tg_id.in_(batch)).values(trial=-1))
            await session.commit()
        logger.info(f"[InactiveTrial] {len(users_to_extend)} пользователей с расширенным триалом")

    if messages:
        results = await send_messages_with_limit(bot, messages)

        sent_tg_ids = [msg["tg_id"] for msg, result in zip(messages, results, strict=False) if result]

        if sent_tg_ids:
            if sessionmaker is not None:
                async with sessionmaker() as fresh_session:
                    for i in range(0, len(sent_tg_ids), _INACTIVE_TRIAL_NOTIFY_BATCH_SIZE):
                        batch = sent_tg_ids[i : i + _INACTIVE_TRIAL_NOTIFY_BATCH_SIZE]
                        await bulk_add_notifications(
                            fresh_session,
                            [(tg_id, "inactive_trial") for tg_id in batch],
                            commit=False,
                        )
                    await fresh_session.commit()
            else:
                for i in range(0, len(sent_tg_ids), _INACTIVE_TRIAL_NOTIFY_BATCH_SIZE):
                    batch = sent_tg_ids[i : i + _INACTIVE_TRIAL_NOTIFY_BATCH_SIZE]
                    await bulk_add_notifications(
                        session,
                        [(tg_id, "inactive_trial") for tg_id in batch],
                        commit=False,
                    )
                await session.commit()
            logger.info(f"[InactiveTrial] Отправлено {len(sent_tg_ids)} уведомлений")
