import asyncio

from datetime import datetime, timedelta

import asyncpg
import pytz

from aiogram import Bot, Router

from config import (
    DATABASE_URL,
    NOTIFICATION_TIME,
    NOTIFY_DELETE_DELAY,
    NOTIFY_DELETE_KEY,
    NOTIFY_INACTIVE_TRAFFIC,
    NOTIFY_MAXPRICE,
    NOTIFY_RENEW,
    NOTIFY_RENEW_EXPIRED,
    RENEWAL_PRICES,
    TOTAL_GB,
    TRIAL_TIME_DISABLE,
)
from database import (
    add_notification,
    check_notification_time,
    check_notifications_bulk,
    delete_key,
    delete_notification,
    get_all_keys,
    get_balance,
    get_last_notification_time,
    update_balance,
    update_key_expiry,
)
from handlers.keys.key_utils import delete_key_from_cluster, renew_key_in_cluster
from handlers.notifications.notify_kb import build_notification_expired_kb, build_notification_kb
from handlers.texts import (
    KEY_DELETED_MSG,
    KEY_EXPIRED_DELAY_HOURS_MINUTES_MSG,
    KEY_EXPIRED_DELAY_HOURS_MSG,
    KEY_EXPIRED_DELAY_MINUTES_MSG,
    KEY_EXPIRED_NO_DELAY_MSG,
    KEY_EXPIRY_10H,
    KEY_EXPIRY_24H,
    KEY_RENEWED,
    KEY_RENEWED_TEMP_MSG,
)
from handlers.utils import format_hours, format_minutes
from logger import logger

from .notify_utils import send_messages_with_limit, send_notification
from .special_notifications import notify_inactive_trial_users, notify_users_no_traffic


router = Router()

moscow_tz = pytz.timezone("Europe/Moscow")


notification_lock = asyncio.Lock()


async def periodic_notifications(bot: Bot):
    """
    Периодическая проверка и отправка уведомлений.
    Защищена от одновременного запуска с помощью asyncio.Lock.
    """
    while True:
        if notification_lock.locked():
            logger.warning("Предыдущая задача уведомлений ещё выполняется. Пропуск итерации.")
            await asyncio.sleep(NOTIFICATION_TIME)
            continue

        async with notification_lock:
            conn = None
            try:
                conn = await asyncpg.connect(DATABASE_URL)
                current_time = int(datetime.now(moscow_tz).timestamp() * 1000)

                threshold_time_10h = int((datetime.now(moscow_tz) + timedelta(hours=10)).timestamp() * 1000)
                threshold_time_24h = int((datetime.now(moscow_tz) + timedelta(days=1)).timestamp() * 1000)

                logger.info("Запуск обработки уведомлений")

                try:
                    keys = await get_all_keys(session=conn)
                    keys = [k for k in keys if not k["is_frozen"]]
                except Exception as e:
                    logger.error(f"Ошибка при получении ключей: {e}")
                    keys = []

                if not TRIAL_TIME_DISABLE:
                    await notify_inactive_trial_users(bot, conn)
                    await asyncio.sleep(0.5)

                await notify_24h_keys(bot, conn, current_time, threshold_time_24h, keys)
                await asyncio.sleep(1)
                await notify_10h_keys(bot, conn, current_time, threshold_time_10h, keys)
                await asyncio.sleep(1)
                await handle_expired_keys(bot, conn, current_time, keys)
                await asyncio.sleep(0.5)
                if NOTIFY_INACTIVE_TRAFFIC:
                    await notify_users_no_traffic(bot, conn, current_time, keys)
                    await asyncio.sleep(0.5)

                logger.info("Завершена обработка уведомлений")

            except Exception as e:
                logger.error(f"Ошибка в periodic_notifications: {e}")
            finally:
                if conn:
                    await conn.close()
                    logger.info("Соединение с базой данных закрыто.")

        await asyncio.sleep(NOTIFICATION_TIME)


async def notify_24h_keys(bot: Bot, conn: asyncpg.Connection, current_time: int, threshold_time_24h: int, keys: list):
    """
    Отправляет уведомления пользователям о том, что их подписка истекает через 24 часа.
    """
    logger.info("Начало проверки подписок, истекающих через 24 часа.")

    expiring_keys = [
        key for key in keys if key.get("expiry_time") and current_time < key.get("expiry_time") <= threshold_time_24h
    ]
    logger.info(f"Найдено {len(expiring_keys)} подписок, истекающих через 24 часа.")

    tg_ids = [key["tg_id"] for key in expiring_keys]
    emails = [key.get("email", "") for key in expiring_keys]

    users = await check_notifications_bulk("key_24h", 24, conn, tg_ids=tg_ids, emails=emails)
    messages = []

    for key in expiring_keys:
        tg_id = key["tg_id"]
        email = key.get("email", "")
        notification_id = f"{email}_key_24h"

        can_notify = await check_notification_time(tg_id, notification_id, hours=24, session=conn)
        if not can_notify:
            continue

        user = next((u for u in users if u["tg_id"] == tg_id and u["email"] == email), None)
        if not user:
            continue

        expiry_timestamp = key.get("expiry_time")
        hours_left = int((expiry_timestamp - current_time) / (1000 * 3600))
        hours_left_formatted = (
            f"⏳ Осталось времени: {format_hours(hours_left)}" if hours_left > 0 else "⏳ Последний день подписки!"
        )

        expiry_datetime = datetime.fromtimestamp(expiry_timestamp / 1000, tz=moscow_tz)
        formatted_expiry_date = expiry_datetime.strftime("%d %B %Y, %H:%M (МСК)")

        notification_text = KEY_EXPIRY_24H.format(
            email=email,
            hours_left_formatted=hours_left_formatted,
            formatted_expiry_date=formatted_expiry_date,
        )

        if NOTIFY_RENEW:
            await process_auto_renew_or_notify(bot, conn, key, notification_id, 1, "notify_24h.jpg", notification_text)
        else:
            keyboard = build_notification_kb(email)
            messages.append({
                "tg_id": tg_id,
                "text": notification_text,
                "photo": "notify_24h.jpg",
                "keyboard": keyboard,
            })
            await add_notification(tg_id, notification_id, session=conn)

    if messages:
        await send_messages_with_limit(bot, messages)
        logger.info(f"Отправлено {len(messages)} уведомлений об истечении подписки через 24 часа.")

    logger.info("Обработка всех уведомлений за 24 часа завершена.")
    await asyncio.sleep(1)


async def notify_10h_keys(bot: Bot, conn: asyncpg.Connection, current_time: int, threshold_time_10h: int, keys: list):
    """
    Отправляет уведомления пользователям о том, что их подписка истекает через 10 часов.
    """
    logger.info("Начало проверки подписок, истекающих через 10 часов.")

    expiring_keys = [
        key for key in keys if key.get("expiry_time") and current_time < key.get("expiry_time") <= threshold_time_10h
    ]
    logger.info(f"Найдено {len(expiring_keys)} подписок, истекающих через 10 часов.")

    tg_ids = [key["tg_id"] for key in expiring_keys]
    emails = [key.get("email", "") for key in expiring_keys]

    users = await check_notifications_bulk("key_10h", 10, conn, tg_ids=tg_ids, emails=emails)
    messages = []

    for key in expiring_keys:
        tg_id = key["tg_id"]
        email = key.get("email", "")
        notification_id = f"{email}_key_10h"

        can_notify = await check_notification_time(tg_id, notification_id, hours=10, session=conn)
        if not can_notify:
            continue

        user = next((u for u in users if u["tg_id"] == tg_id and u["email"] == email), None)
        if not user:
            continue

        expiry_timestamp = key.get("expiry_time")
        hours_left = int((expiry_timestamp - current_time) / (1000 * 3600))
        hours_left_formatted = (
            f"⏳ Осталось времени: {format_hours(hours_left)}" if hours_left > 0 else "⏳ Последний день подписки!"
        )

        expiry_datetime = datetime.fromtimestamp(expiry_timestamp / 1000, tz=moscow_tz)
        formatted_expiry_date = expiry_datetime.strftime("%d %B %Y, %H:%M (МСК)")

        notification_text = KEY_EXPIRY_10H.format(
            email=email,
            hours_left_formatted=hours_left_formatted,
            formatted_expiry_date=formatted_expiry_date,
        )

        if NOTIFY_RENEW:
            try:
                await process_auto_renew_or_notify(
                    bot, conn, key, notification_id, 1, "notify_10h.jpg", notification_text
                )
            except Exception as e:
                logger.error(f"Ошибка авто-продления/уведомления для пользователя {tg_id}: {e}")
        else:
            keyboard = build_notification_kb(email)
            messages.append({
                "tg_id": tg_id,
                "text": notification_text,
                "photo": "notify_10h.jpg",
                "keyboard": keyboard,
            })
            await add_notification(tg_id, notification_id, session=conn)

    if messages:
        await send_messages_with_limit(bot, messages)
        logger.info(f"Отправлено {len(messages)} уведомлений об истечении подписки через 10 часов.")

    logger.info("Обработка всех уведомлений за 10 часов завершена.")
    await asyncio.sleep(1)


async def handle_expired_keys(bot: Bot, conn: asyncpg.Connection, current_time: int, keys: list):
    """
    Обрабатывает истекшие ключи, проверяя продление или удаление.
    """
    logger.info("Начало обработки истекших ключей.")

    expired_keys = [key for key in keys if key.get("expiry_time") and key.get("expiry_time") < current_time]
    logger.info(f"Найдено {len(expired_keys)} истекших ключей.")

    tg_ids = [key["tg_id"] for key in expired_keys]
    emails = [key.get("email", "") for key in expired_keys]

    users = await check_notifications_bulk("key_expired", 0, conn, tg_ids=tg_ids, emails=emails)
    messages = []

    for key in expired_keys:
        tg_id = key["tg_id"]
        email = key.get("email", "")
        client_id = key.get("client_id")
        server_id = key.get("server_id")
        notification_id = f"{email}_key_expired"

        last_notification_time = await get_last_notification_time(tg_id, notification_id, session=conn)

        if NOTIFY_RENEW_EXPIRED:
            try:
                balance = await get_balance(tg_id)
            except Exception as e:
                logger.error(f"Ошибка получения баланса для пользователя {tg_id}: {e}")
                continue

            renewal_period_months = 1
            renewal_cost = RENEWAL_PRICES[str(renewal_period_months)]

            if balance >= renewal_cost:
                try:
                    await process_auto_renew_or_notify(
                        bot, conn, key, notification_id, 1, "notify_expired.jpg", KEY_RENEWED_TEMP_MSG
                    )
                except Exception as e:
                    logger.error(f"Ошибка авто-продления для пользователя {tg_id}: {e}")
                continue

        if NOTIFY_DELETE_KEY:
            delete_immediately = NOTIFY_DELETE_DELAY == 0
            delete_after_delay = False

            if last_notification_time is not None:
                delete_after_delay = (current_time - last_notification_time) / (1000 * 60) >= NOTIFY_DELETE_DELAY
                logger.info(
                    f"Прошло минут={(current_time - last_notification_time) / (1000 * 60):.2f} "
                    f"NOTIFY_DELETE_DELAY={NOTIFY_DELETE_DELAY}"
                )

            if delete_immediately or delete_after_delay:
                try:
                    await delete_key_from_cluster(server_id, email, client_id)
                    await delete_key(client_id, conn)
                    logger.info(f"🗑 Ключ {client_id} для пользователя {tg_id} успешно удалён.")

                    keyboard = build_notification_expired_kb()
                    messages.append({
                        "tg_id": tg_id,
                        "text": KEY_DELETED_MSG.format(email=email),
                        "photo": "notify_expired.jpg",
                        "keyboard": keyboard,
                    })
                except Exception as e:
                    logger.error(f"Ошибка удаления ключа {client_id} для пользователя {tg_id}: {e}")
                continue

        if last_notification_time is None and any(u["tg_id"] == tg_id and u["email"] == email for u in users):
            keyboard = build_notification_kb(email)

            if NOTIFY_DELETE_DELAY > 0:
                hours = NOTIFY_DELETE_DELAY // 60
                minutes = NOTIFY_DELETE_DELAY % 60

                if hours > 0:
                    if minutes > 0:
                        delay_message = KEY_EXPIRED_DELAY_HOURS_MINUTES_MSG.format(
                            email=email, hours_formatted=format_hours(hours), minutes_formatted=format_minutes(minutes)
                        )
                    else:
                        delay_message = KEY_EXPIRED_DELAY_HOURS_MSG.format(
                            email=email, hours_formatted=format_hours(hours)
                        )
                else:
                    delay_message = KEY_EXPIRED_DELAY_MINUTES_MSG.format(
                        email=email, minutes_formatted=format_minutes(minutes)
                    )
            else:
                delay_message = KEY_EXPIRED_NO_DELAY_MSG.format(email=email)

            messages.append({
                "tg_id": tg_id,
                "text": delay_message,
                "photo": "notify_expired.jpg",
                "keyboard": keyboard,
            })
            await add_notification(tg_id, notification_id, session=conn)

    if messages:
        await send_messages_with_limit(bot, messages)
        logger.info(f"Отправлено {len(messages)} уведомлений об истекших ключах.")

    logger.info("Обработка истекших ключей завершена.")
    await asyncio.sleep(1)


async def process_auto_renew_or_notify(
    bot, conn, key: dict, notification_id: str, renewal_period_months: int, standard_photo: str, standard_caption: str
):
    """
    Если баланс пользователя позволяет, продлевает ключ на максимальный возможный срок и списывает средства;
    иначе отправляет стандартное уведомление.
    """
    tg_id = key.get("tg_id")
    email = key.get("email", "")
    renew_notification_id = f"{email}_renew"

    try:
        can_renew = await check_notification_time(tg_id, renew_notification_id, hours=24, session=conn)
        if not can_renew:
            logger.info(
                f"⏳ Подписка {email} уже продлевалась в течение последних 24 часов, повторное продление отменено."
            )
            return

        balance = await get_balance(tg_id)
    except Exception as e:
        logger.error(f"Ошибка получения данных для пользователя {tg_id}: {e}")
        return

    if NOTIFY_MAXPRICE:
        renewal_period_months = max(
            (int(months) for months, price in RENEWAL_PRICES.items() if balance >= price), default=None
        )
    else:
        renewal_period_months = 1 if balance >= RENEWAL_PRICES["1"] else None

    if renewal_period_months:
        renewal_period_months = int(renewal_period_months)
        renewal_cost = RENEWAL_PRICES[str(renewal_period_months)]
        client_id = key.get("client_id")
        server_id = key.get("server_id")
        current_expiry = key.get("expiry_time")
        new_expiry_time = current_expiry + renewal_period_months * 30 * 24 * 3600 * 1000

        formatted_expiry_date = datetime.fromtimestamp(new_expiry_time / 1000, moscow_tz).strftime("%d %B %Y, %H:%M")
        total_gb = int(renewal_period_months * TOTAL_GB * 1024**3)

        logger.info(
            f"[Автопродление] Продление подписки {email} на {renewal_period_months} мес. для пользователя {tg_id}. Баланс: {balance}, списываем: {renewal_cost}"
        )

        try:
            await renew_key_in_cluster(server_id, email, client_id, new_expiry_time, total_gb)
            await update_balance(tg_id, -renewal_cost, session=conn)
            await update_key_expiry(client_id, new_expiry_time, conn)

            await add_notification(tg_id, renew_notification_id, session=conn)
            await delete_notification(tg_id, notification_id, session=conn)

            logger.info(
                f"✅ Ключ {client_id} продлён на {renewal_period_months} мес. для пользователя {tg_id}. Списано {renewal_cost}."
            )

            renewed_message = KEY_RENEWED.format(
                email=email, months=renewal_period_months, expiry_date=formatted_expiry_date
            )

            keyboard = build_notification_expired_kb()
            result = await send_notification(bot, tg_id, "notify_expired.jpg", renewed_message, keyboard)
            if result:
                logger.info(f"✅ Уведомление о продлении подписки {email} отправлено пользователю {tg_id}.")
            else:
                logger.warning(
                    f"📢 Не удалось отправить уведомление о продлении подписки {email} пользователю {tg_id}."
                )
        except KeyError as e:
            logger.error(f"❌ Ошибка форматирования сообщения KEY_RENEWED: отсутствует ключ {e}")
        except Exception as e:
            logger.error(f"❌ Ошибка при продлении ключа {client_id} для пользователя {tg_id}: {e}")
    else:
        keyboard = build_notification_kb(email)
        result = await send_notification(bot, tg_id, standard_photo, standard_caption, keyboard)
        if result:
            logger.info(f"📢 Отправлено уведомление об истекающей подписке {email} пользователю {tg_id}.")
            await add_notification(tg_id, notification_id, session=conn)
        else:
            logger.warning(f"📢 Не удалось отправить уведомление об истекающей подписке {email} пользователю {tg_id}.")
