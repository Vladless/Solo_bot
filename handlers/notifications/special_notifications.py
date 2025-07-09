from datetime import datetime, timedelta

import pytz
from aiogram import Bot, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    NOTIFY_EXTRA_DAYS,
    NOTIFY_INACTIVE,
    NOTIFY_INACTIVE_TRAFFIC,
    SUPPORT_CHAT_URL,
    TRIAL_CONFIG,
)
from database import (
    add_notification,
    check_notifications_bulk,
    mark_trial_extended,
    update_key_notified,
)
from handlers.localization import get_user_texts, get_user_buttons
from handlers.keys.key_utils import get_user_traffic
from handlers.utils import format_days
from logger import logger

from .notify_utils import send_messages_with_limit

router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


async def notify_inactive_trial_users(bot: Bot, session: AsyncSession):
    logger.info("Проверка пользователей, не активировавших пробный период...")
    users = await check_notifications_bulk(session, "inactive_trial", NOTIFY_INACTIVE)
    logger.info(f"Найдено {len(users)} неактивных пользователей для уведомления.")
    messages = []

    trial_days = TRIAL_CONFIG["duration_days"]

    for user in users:
        tg_id = user["tg_id"]
        username = user["username"]
        first_name = user["first_name"]
        last_name = user["last_name"]
        display_name = username or first_name or last_name or buttons.DEFAULT_USER

        texts = await get_user_texts(session, tg_id)
        buttons = await get_user_buttons(session, tg_id)

        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(
                text=buttons.ACTIVATE_TRIAL, callback_data="create_key"
            )
        )
        builder.row(types.InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))
        keyboard = builder.as_markup()

        trial_extended = user["last_notification_time"] is not None

        if trial_extended:
            total_days = NOTIFY_EXTRA_DAYS + trial_days
            message = texts.TRIAL_INACTIVE_BONUS_MSG.format(
                display_name=display_name,
                extra_days_formatted=format_days(NOTIFY_EXTRA_DAYS),
                total_days_formatted=format_days(total_days),
            )
            await mark_trial_extended(tg_id, session)
        else:
            message = texts.TRIAL_INACTIVE_FIRST_MSG.format(
                display_name=display_name,
                trial_time_formatted=format_days(trial_days),
            )

        messages.append(
            {
                "tg_id": tg_id,
                "text": message,
                "keyboard": keyboard,
                "notification_id": "inactive_trial",
            }
        )

    if messages:
        results = await send_messages_with_limit(
            bot,
            messages,
            session=session,
            source_file="special_notifications",
            messages_per_second=25,
        )
        sent_count = 0
        for msg, result in zip(messages, results, strict=False):
            if result:
                await add_notification(session, msg["tg_id"], msg["notification_id"])
                sent_count += 1
        logger.info(f"Отправлено {sent_count} уведомлений неактивным пользователям.")
    logger.info("✅ Проверка пользователей с неактивным пробным периодом завершена.")


async def notify_users_no_traffic(
    bot: Bot, session: AsyncSession, current_time: int, keys: list
):
    logger.info("Проверка пользователей с нулевым трафиком...")
    current_dt = datetime.fromtimestamp(current_time / 1000, tz=moscow_tz)
    messages = []

    for key in keys:
        tg_id = key.tg_id
        email = key.email
        created_at = key.created_at
        client_id = key.client_id
        expiry_time = key.expiry_time
        notified = key.notified

        if created_at is None or notified:
            continue

        created_at_dt = pytz.utc.localize(
            datetime.fromtimestamp(created_at / 1000)
        ).astimezone(moscow_tz)
        if current_dt < created_at_dt + timedelta(hours=NOTIFY_INACTIVE_TRAFFIC):
            continue

        if expiry_time:
            expiry_dt = pytz.utc.localize(
                datetime.fromtimestamp(expiry_time / 1000)
            ).astimezone(moscow_tz)
            if (current_dt - (expiry_dt - timedelta(days=30))) < timedelta(
                hours=NOTIFY_INACTIVE_TRAFFIC
            ):
                continue

        try:
            traffic_data = await get_user_traffic(session, tg_id, email)
        except Exception as e:
            logger.error(f"Ошибка получения трафика для {email}: {e}")
            continue

        if traffic_data.get("status") != "success":
            logger.warning(
                f"⚠ Ошибка при получении трафика для {email}: {traffic_data.get('message')}"
            )
            continue

        total_traffic = sum(
            value if isinstance(value, int | float) else 0
            for value in traffic_data.get("traffic", {}).values()
        )

        try:
            await update_key_notified(session, tg_id, client_id)
        except Exception as e:
            logger.error(f"Ошибка обновления notified для {tg_id} ({client_id}): {e}")
            continue

        if total_traffic == 0:
            logger.info(
                f"⚠ У пользователя {tg_id} ({email}) 0 ГБ трафика. Отправляем уведомление."
            )
            
            texts = await get_user_texts(session, tg_id)
            buttons = await get_user_buttons(session, tg_id)
            
            builder = InlineKeyboardBuilder()
            builder.row(
                types.InlineKeyboardButton(
                    text=buttons.SUPPORT_CONTACT, url=SUPPORT_CHAT_URL
                )
            )
            builder.row(
                types.InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile")
            )
            keyboard = builder.as_markup()
            message = texts.ZERO_TRAFFIC_MSG.format(email=email)
            messages.append(
                {
                    "tg_id": tg_id,
                    "text": message,
                    "keyboard": keyboard,
                    "client_id": client_id,
                }
            )

    if messages:
        results = await send_messages_with_limit(
            bot,
            messages,
            session=session,
            source_file="special_notifications",
            messages_per_second=25,
        )
        sent_count = sum(result for result in results if result)
        logger.info(f"Отправлено {sent_count} уведомлений о нулевом трафике.")

    logger.info("✅ Обработка пользователей с нулевым трафиком завершена.")
