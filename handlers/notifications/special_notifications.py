import asyncio

from datetime import datetime, timedelta

import asyncpg
import pytz

from aiogram import Bot, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    NOTIFY_EXTRA_DAYS,
    NOTIFY_INACTIVE,
    NOTIFY_INACTIVE_TRAFFIC,
    SUPPORT_CHAT_URL,
    TRIAL_TIME,
)
from database import add_notification, check_notifications_bulk, create_blocked_user
from handlers.buttons import MAIN_MENU
from handlers.keys.key_utils import get_user_traffic
from handlers.texts import (
    TRIAL_INACTIVE_BONUS_MSG,
    TRIAL_INACTIVE_FIRST_MSG,
    ZERO_TRAFFIC_MSG,
)
from handlers.utils import format_days
from logger import logger

from .notify_utils import send_messages_with_limit, send_notification


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


async def notify_inactive_trial_users(bot: Bot, conn: asyncpg.Connection):
    """
    Проверяет пользователей, не активировавших пробный период, и отправляет им напоминания.
    Первое уведомление — стандартное.
    Если прошло 24 часа и триал не активирован, отправляется уведомление с бонусом +2 дня.
    """
    logger.info("Проверка пользователей, не активировавших пробный период...")
    users = await check_notifications_bulk("inactive_trial", NOTIFY_INACTIVE, conn)
    logger.info(f"Найдено {len(users)} неактивных пользователей для уведомления.")
    messages = []
    for user in users:
        tg_id = user["tg_id"]
        username = user["username"]
        first_name = user["first_name"]
        last_name = user["last_name"]
        display_name = username or first_name or last_name or "Пользователь"
        builder = InlineKeyboardBuilder()
        builder.row(
            types.InlineKeyboardButton(
                text="🚀 Активировать пробный период",
                callback_data="create_key",
            )
        )
        builder.row(types.InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        keyboard = builder.as_markup()
        trial_extended = user["last_notification_time"] is not None
        if trial_extended:
            total_days = NOTIFY_EXTRA_DAYS + TRIAL_TIME
            message = TRIAL_INACTIVE_BONUS_MSG.format(
                display_name=display_name,
                extra_days_formatted=format_days(NOTIFY_EXTRA_DAYS),
                total_days_formatted=format_days(total_days),
            )
            await conn.execute("UPDATE users SET trial = -1 WHERE tg_id = $1", tg_id)
        else:
            message = TRIAL_INACTIVE_FIRST_MSG.format(
                display_name=display_name, trial_time_formatted=format_days(TRIAL_TIME)
            )
        messages.append({
            "tg_id": tg_id,
            "text": message,
            "keyboard": keyboard,
            "notification_id": "inactive_trial",
        })
    if messages:
        results = await send_messages_with_limit(
            bot, messages, conn=conn, source_file="special_notifications", messages_per_second=25
        )
        sent_count = 0
        for msg, result in zip(messages, results, strict=False):
            tg_id = msg["tg_id"]
            if result:
                await add_notification(tg_id, msg["notification_id"], session=conn)
                sent_count += 1
                logger.info(f"📩 Отправлено уведомление неактивному пользователю {tg_id}.")
            else:
                logger.warning(f"📩 Не удалось отправить уведомление неактивному пользователю {tg_id}.")
        logger.info(f"Отправлено {sent_count} уведомлений неактивным пользователям.")
    logger.info("✅ Проверка пользователей с неактивным пробным периодом завершена.")


async def notify_users_no_traffic(bot: Bot, conn: asyncpg.Connection, current_time: int, keys: list):
    """
    Проверяет трафик пользователей, у которых ещё не отправлялось уведомление о нулевом трафике.
    Если трафик 0 ГБ и прошло более 2 часов с момента создания ключа, отправляет уведомление,
    но исключает пользователей, у которых подписка недавно продлилась.
    """
    logger.info("Проверка пользователей с нулевым трафиком...")
    current_dt = datetime.fromtimestamp(current_time / 1000, tz=moscow_tz)
    messages = []

    for key in keys:
        tg_id = key.get("tg_id")
        email = key.get("email")
        created_at = key.get("created_at")
        client_id = key.get("client_id")
        expiry_time = key.get("expiry_time")
        notified = key.get("notified")

        if created_at is None:
            logger.warning(f"Для {email} нет значения created_at. Пропускаем.")
            continue

        if notified is True:
            continue

        created_at_dt = pytz.utc.localize(datetime.fromtimestamp(created_at / 1000)).astimezone(moscow_tz)
        created_at_plus_2 = created_at_dt + timedelta(hours=NOTIFY_INACTIVE_TRAFFIC)

        if expiry_time:
            expiry_dt = pytz.utc.localize(datetime.fromtimestamp(expiry_time / 1000)).astimezone(moscow_tz)
            renewal_threshold = expiry_dt - timedelta(days=30)
            renewal_recent = current_dt - renewal_threshold < timedelta(hours=NOTIFY_INACTIVE_TRAFFIC)
            if renewal_recent:
                continue

        if current_dt < created_at_plus_2:
            continue

        try:
            traffic_data = await get_user_traffic(conn, tg_id, email)
        except Exception as e:
            logger.error(f"Ошибка получения трафика для {email}: {e}")
            continue

        if traffic_data.get("status") != "success":
            logger.warning(f"⚠ Ошибка при получении трафика для {email}: {traffic_data.get('message')}")
            continue

        total_traffic = sum(
            value if isinstance(value, int | float) else 0 for value in traffic_data.get("traffic", {}).values()
        )

        try:
            await conn.execute("UPDATE keys SET notified = TRUE WHERE tg_id = $1 AND client_id = $2", tg_id, client_id)
        except Exception as e:
            logger.error(f"Ошибка обновления notified для пользователя {tg_id} (client_id: {client_id}): {e}")
            continue

        if total_traffic == 0:
            logger.info(f"⚠ У пользователя {tg_id} ({email}) 0 ГБ трафика. Отправляем уведомление.")
            builder = InlineKeyboardBuilder()
            builder.row(types.InlineKeyboardButton(text="🔧 Написать в поддержку", url=SUPPORT_CHAT_URL))
            builder.row(types.InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            keyboard = builder.as_markup()
            message = ZERO_TRAFFIC_MSG.format(email=email)
            messages.append({
                "tg_id": tg_id,
                "text": message,
                "keyboard": keyboard,
                "client_id": client_id,
            })

    if messages:
        results = await send_messages_with_limit(
            bot, messages, conn=conn, source_file="special_notifications", messages_per_second=25
        )
        sent_count = 0
        for msg, result in zip(messages, results, strict=False):
            tg_id = msg["tg_id"]
            if result:
                sent_count += 1
                logger.info(f"📩 Отправлено уведомление пользователю {tg_id} о нулевом трафике.")
            else:
                logger.warning(f"📩 Не удалось отправить уведомление пользователю {tg_id} о нулевом трафике.")
        logger.info(f"Отправлено {sent_count} уведомлений о нулевом трафике.")

    logger.info("✅ Обработка пользователей с нулевым трафиком завершена.")
