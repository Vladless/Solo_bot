import asyncio
import os
from datetime import datetime, timedelta

import aiofiles
import asyncpg
import pytz
from aiogram import Bot, Router, types
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from py3xui import AsyncApi

from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    AUTO_DELETE_EXPIRED_KEYS,
    AUTO_RENEW_KEYS,
    DATABASE_URL,
    DEV_MODE,
    EXPIRED_KEYS_CHECK_INTERVAL,
    RENEWAL_PLANS,
    TOTAL_GB,
    TRIAL_TIME,
)
from database import (
    add_notification,
    check_notification_time,
    create_blocked_user,
    delete_key,
    get_balance,
    get_servers,
    update_balance,
    update_key_expiry,
)
from handlers.keys.key_utils import delete_key_from_cluster, renew_key_in_cluster
from handlers.texts import KEY_EXPIRY_10H, KEY_EXPIRY_24H, KEY_RENEWED
from logger import logger

router = Router()


async def periodic_expired_keys_check(bot: Bot):
    """Периодическая проверка истекших ключей с кастомным интервалом."""
    while True:
        conn = None
        try:
            conn = await asyncpg.connect(DATABASE_URL)
            current_time = int(datetime.utcnow().timestamp() * 1000)
            await handle_expired_keys(bot, conn, current_time)
            logger.info("✅ Проверка истекших ключей выполнена.")
        except Exception as e:
            logger.error(f"❌ Ошибка в periodic_expired_keys_check: {e}")
        finally:
            if conn:
                await conn.close()

        await asyncio.sleep(EXPIRED_KEYS_CHECK_INTERVAL)


async def notify_expiring_keys(bot: Bot):
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info("Подключение к базе данных успешно.")

        current_time = int(datetime.utcnow().timestamp() * 1000)
        threshold_time_10h = int((datetime.utcnow() + timedelta(hours=10)).timestamp() * 1000)
        threshold_time_24h = int((datetime.utcnow() + timedelta(days=1)).timestamp() * 1000)

        logger.info("Начало обработки уведомлений.")

        await notify_inactive_trial_users(bot, conn)
        await asyncio.sleep(0.5)
        await check_online_users()
        await asyncio.sleep(0.5)
        await notify_10h_keys(bot, conn, current_time, threshold_time_10h)
        await asyncio.sleep(0.5)
        await notify_24h_keys(bot, conn, current_time, threshold_time_24h)
        await asyncio.sleep(0.5)

    except Exception as e:
        logger.error(f"Ошибка при отправке уведомлений: {e}")
    finally:
        if conn:
            await conn.close()
            logger.info("Соединение с базой данных закрыто.")


async def is_bot_blocked(bot: Bot, chat_id: int) -> bool:
    if DEV_MODE:
        return False
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        blocked = member.status == "left"
        logger.info(f"Статус бота для пользователя {chat_id}: {'заблокирован' if blocked else 'активен'}")
        return blocked
    except Exception as e:
        logger.warning(f"Не удалось проверить статус бота для пользователя {chat_id}: {e}")
        return False


async def notify_10h_keys(
    bot: Bot,
    conn: asyncpg.Connection,
    current_time: float,
    threshold_time_10h: float,
):
    records = await conn.fetch(
        """
        SELECT tg_id, email, expiry_time, client_id, server_id FROM keys 
        WHERE expiry_time <= $1 AND expiry_time > $2 AND notified = FALSE
        """,
        threshold_time_10h,
        current_time,
    )

    logger.info(f"Найдено {len(records)} ключей для уведомления за 10 часов.")

    for record in records:
        await process_10h_record(record, bot, conn)

    logger.info("Обработка всех уведомлений за 10 часов завершена.")


async def process_10h_record(record, bot, conn):
    tg_id = record["tg_id"]
    email = record["email"]
    expiry_time = record["expiry_time"]

    moscow_tz = pytz.timezone("Europe/Moscow")

    expiry_date = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz)
    current_date = datetime.now(moscow_tz)
    time_left = expiry_date - current_date

    days_left_message = (
        "Ключ истек"
        if time_left.total_seconds() <= 0
        else f"{time_left.days}"
        if time_left.days > 0
        else f"{time_left.seconds // 3600}"
    )

    message = KEY_EXPIRY_10H.format(
        email=email,
        expiry_date=expiry_date.strftime("%Y-%m-%d %H:%M:%S"),
        days_left_message=days_left_message,
        price=RENEWAL_PLANS["1"]["price"],
    )

    balance = await get_balance(tg_id)

    if AUTO_RENEW_KEYS and balance >= RENEWAL_PLANS["1"]["price"]:
        try:
            await update_balance(tg_id, -RENEWAL_PLANS["1"]["price"], conn)
            new_expiry_time = int((datetime.utcnow() + timedelta(days=30)).timestamp() * 1000)
            await update_key_expiry(record["client_id"], new_expiry_time, conn)

            servers = await get_servers(conn)
            for cluster_id in servers:
                await renew_key_in_cluster(cluster_id, email, record["client_id"], new_expiry_time, TOTAL_GB)
                logger.info(f"Ключ для пользователя {tg_id} успешно продлен в кластере {cluster_id}.")

            await conn.execute("UPDATE keys SET notified = TRUE WHERE client_id = $1", record["client_id"])

            image_path = os.path.join("img", "notify_10h.jpg")
            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")]]
            )

            if os.path.isfile(image_path):
                async with aiofiles.open(image_path, "rb") as image_file:
                    image_data = await image_file.read()
                    await bot.send_photo(
                        tg_id,
                        photo=BufferedInputFile(image_data, filename="notify_10h.jpg"),
                        caption=KEY_RENEWED.format(email=email),
                        reply_markup=keyboard,
                    )
            else:
                await bot.send_message(tg_id, text=KEY_RENEWED.format(email=email), reply_markup=keyboard)

            logger.info(f"Уведомление об успешном продлении отправлено клиенту {tg_id}.")

        except Exception as e:
            logger.error(f"Ошибка при продлении подписки для клиента {tg_id}: {e}")
    else:
        await send_renewal_notification(bot, tg_id, email, message, conn, record["client_id"], "notified")


async def notify_24h_keys(
    bot: Bot,
    conn: asyncpg.Connection,
    current_time: float,
    threshold_time_24h: float,
):
    logger.info("Проверка истекших ключей...")

    records_24h = await conn.fetch(
        """
        SELECT tg_id, email, expiry_time, client_id, server_id FROM keys 
        WHERE expiry_time <= $1 AND expiry_time > $2 AND notified_24h = FALSE
        """,
        threshold_time_24h,
        current_time,
    )

    logger.info(f"Найдено {len(records_24h)} ключей для уведомления за 24 часа.")

    for record in records_24h:
        await process_24h_record(record, bot, conn)

    logger.info("Обработка всех уведомлений за 24 часа завершена.")


async def process_24h_record(record, bot, conn):
    tg_id = record["tg_id"]
    email = record["email"]
    expiry_time = record["expiry_time"]

    moscow_tz = pytz.timezone("Europe/Moscow")

    expiry_date = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz)
    current_date = datetime.now(moscow_tz)
    time_left = expiry_date - current_date

    days_left_message = (
        "Ключ истек"
        if time_left.total_seconds() <= 0
        else f"{time_left.days}"
        if time_left.days > 0
        else f"{time_left.seconds // 3600}"
    )

    message_24h = KEY_EXPIRY_24H.format(
        email=email,
        days_left_message=days_left_message,
        expiry_date=expiry_date.strftime("%Y-%m-%d %H:%M:%S"),
    )

    balance = await get_balance(tg_id)

    if AUTO_RENEW_KEYS and balance >= RENEWAL_PLANS["1"]["price"]:
        try:
            await update_balance(tg_id, -RENEWAL_PLANS["1"]["price"], conn)
            new_expiry_time = int((datetime.utcnow() + timedelta(days=30)).timestamp() * 1000)
            await update_key_expiry(record["client_id"], new_expiry_time, conn)

            servers = await get_servers(conn)
            for cluster_id in servers:
                await renew_key_in_cluster(cluster_id, email, record["client_id"], new_expiry_time, TOTAL_GB)
                logger.info(f"Ключ для пользователя {tg_id} успешно продлен в кластере {cluster_id}.")

            await conn.execute("UPDATE keys SET notified_24h = TRUE WHERE client_id = $1", record["client_id"])

            image_path = os.path.join("img", "notify_24h.jpg")
            keyboard = types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")]]
            )

            if os.path.isfile(image_path):
                async with aiofiles.open(image_path, "rb") as image_file:
                    image_data = await image_file.read()
                    await bot.send_photo(
                        tg_id,
                        photo=BufferedInputFile(image_data, filename="notify_24h.jpg"),
                        caption=KEY_RENEWED.format(email=email),
                        reply_markup=keyboard,
                    )
            else:
                await bot.send_message(tg_id, text=KEY_RENEWED.format(email=email), reply_markup=keyboard)

            logger.info(f"Уведомление об успешном продлении отправлено клиенту {tg_id}.")

        except Exception as e:
            logger.error(f"Ошибка при продлении подписки для клиента {tg_id}: {e}")
    else:
        await send_renewal_notification(bot, tg_id, email, message_24h, conn, record["client_id"], "notified_24h")


async def send_renewal_notification(bot, tg_id, email, message, conn, client_id, flag):
    try:
        keyboard = InlineKeyboardBuilder()
        keyboard.row(types.InlineKeyboardButton(text="🔄 Продлить VPN", callback_data=f"renew_key|{email}"))
        keyboard.row(types.InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="pay"))
        keyboard.row(types.InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

        image_path = os.path.join("img", "notify_24h.jpg")

        if os.path.isfile(image_path):
            async with aiofiles.open(image_path, "rb") as image_file:
                image_data = await image_file.read()
                await bot.send_photo(
                    tg_id,
                    photo=BufferedInputFile(image_data, filename="notify_24h.jpg"),
                    caption=message,
                    reply_markup=keyboard.as_markup(),
                )
        else:
            await bot.send_message(tg_id, message, reply_markup=keyboard.as_markup())

        logger.info(f"Уведомление отправлено пользователю {tg_id}.")

        await conn.execute("UPDATE keys SET notified_24h = $1 WHERE client_id = $2", flag, client_id)

    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю {tg_id}: {e}")


async def notify_inactive_trial_users(bot: Bot, conn: asyncpg.Connection):
    logger.info("Проверка пользователей, не активировавших пробный период...")

    inactive_trial_users = await conn.fetch(
        """
        SELECT tg_id, username, first_name, last_name FROM users 
        WHERE tg_id IN (
            SELECT tg_id FROM connections 
            WHERE trial = 0
        ) AND tg_id NOT IN (
            SELECT DISTINCT tg_id FROM keys
        )
        """
    )
    logger.info(f"Найдено {len(inactive_trial_users)} неактивных пользователей.")

    for user in inactive_trial_users:
        tg_id = user["tg_id"]

        username = user["username"]
        first_name = user["first_name"]
        last_name = user["last_name"]
        display_name = username or first_name or last_name or "Пользователь"

        try:
            can_notify = await check_notification_time(tg_id, "inactive_trial", hours=24, session=conn)

            if can_notify:
                builder = InlineKeyboardBuilder()
                builder.row(
                    types.InlineKeyboardButton(
                        text="🚀 Активировать пробный период",
                        callback_data="create_key",
                    )
                )
                builder.row(types.InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
                keyboard = builder.as_markup()

                message = (
                    f"👋 Привет, {display_name}!\n\n"
                    f"🎉 У тебя есть бесплатный пробный период на {TRIAL_TIME} дней!\n"
                    "🕒 Не упусти возможность попробовать наш VPN прямо сейчас.\n\n"
                    "💡 Нажми на кнопку ниже, чтобы активировать пробный доступ."
                )

                try:
                    await bot.send_message(tg_id, message, reply_markup=keyboard)
                    logger.info(f"Отправлено уведомление неактивному пользователю {tg_id}.")
                    await add_notification(tg_id, "inactive_trial", session=conn)

                except TelegramForbiddenError:
                    logger.warning(f"Бот заблокирован пользователем {tg_id}. Добавляем в blocked_users.")
                    await create_blocked_user(tg_id, conn)
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления пользователю {tg_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка при обработке пользователя {tg_id}: {e}")

        await asyncio.sleep(1)


async def handle_expired_keys(bot: Bot, conn: asyncpg.Connection, current_time: float):
    logger.info("Проверка подписок, срок действия которых скоро истекает...")

    threshold_time = int((datetime.utcnow() + timedelta(minutes=45)).timestamp() * 1000)

    expiring_keys = await conn.fetch(
        """
        SELECT tg_id, client_id, expiry_time, email, server_id FROM keys 
        WHERE expiry_time <= $1 AND expiry_time > $2
        """,
        threshold_time,
        current_time,
    )

    logger.info(f"Найдено {len(expiring_keys)} подписок, срок действия которых скоро истекает.")

    for record in expiring_keys:
        try:
            await process_key(record, bot, conn)
        except Exception as e:
            logger.error(f"Ошибка при обработке подписки {record['client_id']}: {e}")

    expired_keys = await conn.fetch(
        """
        SELECT tg_id, client_id, email, server_id FROM keys 
        WHERE expiry_time <= $1
        """,
        current_time,
    )

    logger.info(f"Найдено {len(expired_keys)} истёкших ключей.")

    for record in expired_keys:
        try:
            await delete_key_from_cluster(record["server_id"], record["email"], record["email"])
            await delete_key(record["client_id"], conn)
            logger.info(f"Удалён истёкший ключ {record['client_id']} пользователя {record['tg_id']}.")
        except Exception as e:
            logger.error(f"Ошибка при удалении истёкшего ключа {record['client_id']}: {e}")


async def process_key(record, bot, conn):
    tg_id = record["tg_id"]
    client_id = record["client_id"]
    email = record["email"]
    balance = await get_balance(tg_id)
    expiry_time = record["expiry_time"]

    moscow_tz = pytz.timezone("Europe/Moscow")
    expiry_date = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz)
    current_date = datetime.now(moscow_tz)
    time_left = expiry_date - current_date

    logger.info(
        f"Время истечения ключа: {expiry_time} (МСК: {expiry_date}), "
        f"Текущее время (МСК): {current_date}, "
        f"Оставшееся время: {time_left}"
    )

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")]]
    )

    image_path = os.path.join("img", "notify_expired.jpg")

    try:
        if AUTO_RENEW_KEYS and balance >= RENEWAL_PLANS["1"]["price"]:
            await update_balance(tg_id, -RENEWAL_PLANS["1"]["price"], conn)

            new_expiry_time = int((datetime.now(moscow_tz) + timedelta(days=30)).timestamp() * 1000)
            await update_key_expiry(client_id, new_expiry_time, conn)

            servers = await get_servers(conn)

            for cluster_id in servers:
                await renew_key_in_cluster(cluster_id, email, client_id, new_expiry_time, TOTAL_GB)
                logger.info(f"Ключ для пользователя {tg_id} успешно продлен в кластере {cluster_id}.")

            await conn.execute(
                """
                UPDATE keys
                SET notified = FALSE, notified_24h = FALSE
                WHERE client_id = $1
                """,
                client_id,
            )
            logger.info(f"Флаги notified сброшены для клиента {client_id}.")

            try:
                if os.path.isfile(image_path):
                    async with aiofiles.open(image_path, "rb") as image_file:
                        image_data = await image_file.read()
                        await bot.send_photo(
                            tg_id,
                            photo=BufferedInputFile(image_data, filename="notify_expired.jpg"),
                            caption=KEY_RENEWED.format(email=email),
                            reply_markup=keyboard,
                        )
                else:
                    await bot.send_message(tg_id, text=KEY_RENEWED, reply_markup=keyboard)
                logger.info(f"Уведомление об успешном продлении отправлено клиенту {tg_id}.")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление о продлении клиенту {tg_id}: {e}")

        else:
            message_expired = f"Ваша подписка {email} истекла. Пополните баланс для продления."
            try:
                if os.path.isfile(image_path):
                    async with aiofiles.open(image_path, "rb") as image_file:
                        image_data = await image_file.read()
                        await bot.send_photo(
                            tg_id,
                            photo=BufferedInputFile(image_data, filename="notify_expired.jpg"),
                            caption=message_expired,
                            reply_markup=keyboard,
                        )
                else:
                    await bot.send_message(tg_id, text=message_expired, reply_markup=keyboard)
                logger.info(f"Уведомление об истечении подписки отправлено пользователю {tg_id}.")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление об истечении клиенту {tg_id}: {e}")

            if AUTO_DELETE_EXPIRED_KEYS:
                servers = await get_servers(conn)

                for cluster_id in servers:
                    try:
                        await delete_key_from_cluster(cluster_id, email, client_id)
                        logger.info(f"Клиент {client_id} удален из кластера {cluster_id}.")
                    except Exception as e:
                        logger.error(f"Ошибка при удалении клиента {client_id} из кластера {cluster_id}: {e}")

                try:
                    await delete_key(client_id, conn)
                    logger.info(f"Ключ {client_id} удалён из базы данных.")
                except Exception as e:
                    logger.error(f"Ошибка при удалении ключа {client_id} из базы данных: {e}")
            else:
                logger.info(f"Ключ {client_id} НЕ был удалён (AUTO_DELETE_EXPIRED_KEYS=False).")

    except Exception as e:
        logger.error(f"Ошибка при обработке ключа для клиента {tg_id}: {e}")


async def check_online_users():
    servers = await get_servers()

    for _cluster_id, cluster in servers.items():
        for server_id, server in enumerate(cluster):
            xui = AsyncApi(server["api_url"], username=ADMIN_USERNAME, password=ADMIN_PASSWORD)
            await xui.login()
            try:
                online_users = len(await xui.client.online())
                logger.info(
                    f"Сервер '{server['server_name']}' доступен, текущее количество активных пользователей: {online_users}."
                )
            except Exception as e:
                logger.error(f"Не удалось проверить пользователей на сервере {server_id}: {e}")
