from datetime import datetime, timedelta

import pytz

from aiogram import Bot, Router, types
from aiogram.types import InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    CONNECT_PHONE_BUTTON,
    NOTIFY_EXTRA_DAYS,
    NOTIFY_INACTIVE,
    NOTIFY_INACTIVE_TRAFFIC,
    REMNAWAVE_WEBAPP,
    SUPPORT_CHAT_URL,
)
from database import (
    add_notification,
    check_notifications_bulk,
    mark_trial_extended,
    update_key_notified,
)
from database.tariffs import get_tariffs
from handlers.buttons import CONNECT_DEVICE, CONNECT_PHONE, MAIN_MENU, PC_BUTTON, TV_BUTTON
from handlers.keys.operations import get_user_traffic
from handlers.notifications.notify_utils import send_messages_with_limit
from handlers.texts import (
    TRIAL_INACTIVE_BONUS_MSG,
    TRIAL_INACTIVE_FIRST_MSG,
    ZERO_TRAFFIC_MSG,
)
from handlers.utils import format_days, is_full_remnawave_cluster
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from logger import logger


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


async def notify_inactive_trial_users(bot: Bot, session: AsyncSession):
    logger.info("Проверка пользователей, не активировавших пробный период...")
    users = await check_notifications_bulk(session, "inactive_trial", NOTIFY_INACTIVE)
    logger.info(f"Найдено {len(users)} неактивных пользователей для уведомления.")
    messages = []

    trial_tariffs = await get_tariffs(session, group_code="trial")
    if not trial_tariffs:
        logger.error("[Notifications] Триальный тариф не найден")
        return

    trial_days = trial_tariffs[0]["duration_days"]

    for user in users:
        tg_id = user["tg_id"]
        username = user["username"]
        first_name = user["first_name"]
        last_name = user["last_name"]
        display_name = username or first_name or last_name or "Пользователь"

        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="🚀 Активировать пробный период", callback_data="create_key"))
        builder.row(types.InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        keyboard = builder.as_markup()

        trial_extended = user["last_notification_time"] is not None

        if trial_extended:
            total_days = NOTIFY_EXTRA_DAYS + trial_days
            message = TRIAL_INACTIVE_BONUS_MSG.format(
                display_name=display_name,
                extra_days_formatted=format_days(NOTIFY_EXTRA_DAYS),
                total_days_formatted=format_days(total_days),
            )
            await mark_trial_extended(tg_id, session)
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
    logger.info("Проверка пользователей с неактивным пробным периодом завершена.")


async def notify_users_no_traffic(bot: Bot, session: AsyncSession, current_time: int, keys: list):
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

        created_at_dt = pytz.utc.localize(datetime.fromtimestamp(created_at / 1000)).astimezone(moscow_tz)
        if current_dt < created_at_dt + timedelta(hours=NOTIFY_INACTIVE_TRAFFIC):
            continue

        if expiry_time:
            expiry_dt = pytz.utc.localize(datetime.fromtimestamp(expiry_time / 1000)).astimezone(moscow_tz)
            if current_dt > expiry_dt:
                continue

        try:
            traffic_data = await get_user_traffic(session, tg_id, email)
        except Exception as e:
            logger.error(f"Ошибка получения трафика для {email}: {e}")
            continue

        if traffic_data.get("status") != "success":
            logger.warning(f"⚠ Ошибка при получении трафика для {email}: {traffic_data.get('message')}")
            continue

        total_traffic = sum(
            value if isinstance(value, int | float) else 0 for value in traffic_data.get("traffic", {}).values()
        )

        if total_traffic == 0:
            logger.info(f"⚠ У пользователя {tg_id} ({email}) 0 ГБ трафика. Отправляем уведомление.")
            builder = InlineKeyboardBuilder()

            server_id = key.server_id
            try:
                is_full_remnawave = await is_full_remnawave_cluster(server_id, session)
                final_link = key.key or key.remnawave_link

                if is_full_remnawave and final_link and REMNAWAVE_WEBAPP:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=final_link)))
                else:
                    if CONNECT_PHONE_BUTTON:
                        builder.row(InlineKeyboardButton(text=CONNECT_PHONE, callback_data=f"connect_phone|{email}"))
                        builder.row(
                            InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{email}"),
                            InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"),
                        )
                    else:
                        builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{email}"))
            except Exception as e:
                logger.error(f"Ошибка при определении типа панели для {email}: {e}")
                builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{email}"))

            builder.row(InlineKeyboardButton(text="🔧 Написать в поддержку", url=SUPPORT_CHAT_URL))
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

            try:
                hook_commands = await run_hooks(
                    "zero_traffic_notification", chat_id=tg_id, admin=False, session=session, email=email
                )
                if hook_commands:
                    builder = insert_hook_buttons(builder, hook_commands)
            except Exception as e:
                logger.warning(f"[ZERO_TRAFFIC_NOTIFICATION] Ошибка при применении хуков: {e}")

            keyboard = builder.as_markup()
            message = ZERO_TRAFFIC_MSG.format(email=email)
            messages.append({
                "tg_id": tg_id,
                "text": message,
                "keyboard": keyboard,
                "client_id": client_id,
            })

        try:
            await update_key_notified(session, tg_id, client_id)
        except Exception as e:
            logger.error(f"Ошибка обновления notified для {tg_id} ({client_id}): {e}")

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
