from __future__ import annotations

from html import escape

from aiogram import Bot, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.bootstrap import NOTIFICATIONS_CONFIG
from database import check_notifications_bulk
from database.access.resolution import UserId, notify_telegram_chat_id
from database.notifications import bulk_add_notifications
from database.tariffs import get_tariffs
from database.users import grant_extended_trial_for_users
from handlers.notifications.sender import send_messages_with_limit
from logger import logger
from services.formatting import format_days
from services.tariffs.trial import select_trial_tariff
from settings.buttons import MAIN_MENU, TRIAL_BONUS
from settings.config import NOTIFY_EXTRA_DAYS, NOTIFY_INACTIVE
from settings.texts import (
    TRIAL_INACTIVE_BONUS_MSG,
    TRIAL_INACTIVE_FIRST_MSG,
    TRIAL_INACTIVE_PAID_BONUS_MSG,
    TRIAL_INACTIVE_PAID_FIRST_MSG,
)


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
    trial_tariff = select_trial_tariff(trial_tariffs)
    if trial_tariff is None:
        logger.error("[InactiveTrial] Триальный тариф не найден")
        return

    trial_days = int(trial_tariff["duration_days"])
    trial_price = max(0, int(trial_tariff.get("price_rub") or 0))
    messages = []
    users_to_extend = []

    for user in users:
        tg_id = await notify_telegram_chat_id(session, UserId(user["user_id"]))
        if tg_id is None:
            continue
        display_name = escape(user["username"] or user["first_name"] or user["last_name"] or "Пользователь")

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text=TRIAL_BONUS, callback_data="create_key"))
        builder.row(types.InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        keyboard = builder.as_markup()

        already_notified = user["last_notification_time"] is not None
        bonus_granted = int(user.get("trial") or 0) == -1

        if already_notified and extra_days > 0 and not bonus_granted:
            total_days = extra_days + trial_days
            template = TRIAL_INACTIVE_PAID_BONUS_MSG if trial_price > 0 else TRIAL_INACTIVE_BONUS_MSG
            message = template.format(
                display_name=display_name,
                extra_days_formatted=format_days(extra_days),
                total_days_formatted=format_days(total_days),
                price_rub=trial_price,
            )
            users_to_extend.append(UserId(user["user_id"]))
        else:
            total_days = trial_days + max(0, extra_days) if bonus_granted else trial_days
            template = TRIAL_INACTIVE_PAID_FIRST_MSG if trial_price > 0 else TRIAL_INACTIVE_FIRST_MSG
            message = template.format(
                display_name=display_name,
                trial_time_formatted=format_days(total_days),
                price_rub=trial_price,
            )

        messages.append({
            "user_id": UserId(user["user_id"]),
            "tg_id": tg_id,
            "text": message,
            "keyboard": keyboard,
            "notification_id": "inactive_trial",
        })

    if users_to_extend:
        for i in range(0, len(users_to_extend), _INACTIVE_TRIAL_UPDATE_BATCH_SIZE):
            batch = users_to_extend[i : i + _INACTIVE_TRIAL_UPDATE_BATCH_SIZE]
            await grant_extended_trial_for_users(session, batch)
            await session.commit()
        logger.info(f"[InactiveTrial] {len(users_to_extend)} пользователей с расширенным триалом")

    if messages:
        results = await send_messages_with_limit(bot, messages)

        sent_user_ids = [msg["user_id"] for msg, result in zip(messages, results, strict=False) if result]

        if sent_user_ids:
            if sessionmaker is not None:
                async with sessionmaker() as fresh_session:
                    for i in range(0, len(sent_user_ids), _INACTIVE_TRIAL_NOTIFY_BATCH_SIZE):
                        batch = sent_user_ids[i : i + _INACTIVE_TRIAL_NOTIFY_BATCH_SIZE]
                        await bulk_add_notifications(
                            fresh_session,
                            [(user_id, "inactive_trial") for user_id in batch],
                            commit=False,
                        )
                    await fresh_session.commit()
            else:
                for i in range(0, len(sent_user_ids), _INACTIVE_TRIAL_NOTIFY_BATCH_SIZE):
                    batch = sent_user_ids[i : i + _INACTIVE_TRIAL_NOTIFY_BATCH_SIZE]
                    await bulk_add_notifications(
                        session,
                        [(user_id, "inactive_trial") for user_id in batch],
                        commit=False,
                    )
                await session.commit()
            logger.info(f"[InactiveTrial] Отправлено {len(sent_user_ids)} уведомлений")
