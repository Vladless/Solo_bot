import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Router, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
import asyncpg
from py3xui import AsyncApi

from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL, DEV_MODE, RENEWAL_PLANS, TOTAL_GB, TRIAL_TIME
from database import (
    add_notification,
    check_notification_time,
    delete_key,
    get_balance,
    get_servers_from_db,
    update_balance,
    update_key_expiry,
)
from routers.handlers.keys.key_utils import delete_key_from_cluster, renew_key_in_cluster
from routers.handlers import KEY_EXPIRY_10H, KEY_EXPIRY_24H, KEY_RENEWED
from logger import logger

router = Router(name=__name__)


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
        await asyncio.sleep(1)
        await check_online_users()
        await asyncio.sleep(1)
        await notify_10h_keys(bot, conn, current_time, threshold_time_10h)
        await asyncio.sleep(1)
        await notify_24h_keys(bot, conn, current_time, threshold_time_24h)
        await asyncio.sleep(1)
        await handle_expired_keys(bot, conn, current_time)
        await asyncio.sleep(1)

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
        tg_id = record["tg_id"]
        email = record["email"]
        expiry_time = record["expiry_time"]

        expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
        current_date = datetime.utcnow()
        time_left = expiry_date - current_date

        if time_left.total_seconds() <= 0:
            days_left_message = "Ключ истек"
        elif time_left.days > 0:
            days_left_message = f"{time_left.days}"
        else:
            hours_left = time_left.seconds // 3600
            days_left_message = f"{hours_left}"

        message = KEY_EXPIRY_10H.format(
            email=email,
            expiry_date=expiry_date.strftime("%Y-%m-%d %H:%M:%S"),
            days_left_message=days_left_message,
            price=RENEWAL_PLANS["1"]["price"],
        )

        if not await is_bot_blocked(bot, tg_id) and not DEV_MODE:
            try:
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="🔄 Продлить VPN", callback_data=f'renew_key|{email}')
                keyboard.button(text="💳 Пополнить баланс", callback_data="pay")
                keyboard.button(text="👤 Личный кабинет", callback_data="profile")
                keyboard.adjust(1)
                keyboard = keyboard.as_markup()
                await bot.send_message(tg_id, message, reply_markup=keyboard)
                logger.info(f"Уведомление отправлено пользователю {tg_id}.")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления пользователю {tg_id}: {e}")
                continue

            await conn.execute(
                "UPDATE keys SET notified = TRUE WHERE client_id = $1",
                record["client_id"],
            )
            logger.info(f"Обновлено поле notified для клиента {record['client_id']}.")

        await asyncio.sleep(1)


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
        tg_id = record["tg_id"]
        email = record["email"]
        expiry_time = record["expiry_time"]

        expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
        current_date = datetime.utcnow()
        time_left = expiry_date - current_date

        if time_left.total_seconds() <= 0:
            days_left_message = "Ключ истек"
        elif time_left.days > 0:
            days_left_message = f"{time_left.days}"
        else:
            hours_left = time_left.seconds // 3600
            days_left_message = f"{hours_left}"

        message_24h = KEY_EXPIRY_24H.format(
            email=email,
            days_left_message=days_left_message,
            expiry_date=expiry_date.strftime("%Y-%m-%d %H:%M:%S"),
        )

        if not await is_bot_blocked(bot, tg_id) and not DEV_MODE:
            try:
                builder = InlineKeyboardBuilder()
                builder.row(
                    types.InlineKeyboardButton(
                        text="🔄 Продлить VPN",
                        callback_data=f'renew_key|{email}',
                    )
                )
                builder.row(
                    types.InlineKeyboardButton(
                        text="💳 Пополнить баланс",
                        callback_data="pay",
                    )
                )
                builder.row(
                    types.InlineKeyboardButton(
                        text="👤 Личный кабинет",
                        callback_data="profile",
                    )
                )
                keyboard = builder.as_markup()
                await bot.send_message(tg_id, message_24h, reply_markup=keyboard)
                logger.info(f"Уведомление за 24 часа отправлено пользователю {tg_id}.")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления за 24 часа пользователю {tg_id}: {e}")
                continue

            await conn.execute(
                "UPDATE keys SET notified_24h = TRUE WHERE client_id = $1",
                record["client_id"],
            )
            logger.info(f"Обновлено поле notified_24h для клиента {record['client_id']}.")

        await asyncio.sleep(1)


async def notify_inactive_trial_users(bot: Bot, conn: asyncpg.Connection):
    logger.info("Проверка пользователей, не активировавших пробный период...")

    inactive_trial_users = await conn.fetch(
        """
        SELECT tg_id, username FROM users 
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
        tg_id = user['tg_id']
        username = user.get('username', 'Пользователь')

        try:
            can_notify = await check_notification_time(tg_id, 'inactive_trial', hours=24, session=conn)

            if can_notify and not await is_bot_blocked(bot, tg_id):
                builder = InlineKeyboardBuilder()
                builder.row(
                    types.InlineKeyboardButton(text="🚀 Активировать пробный период", callback_data="create_key")
                )
                builder.row(types.InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
                keyboard = builder.as_markup()

                message = (
                    f"👋 Привет, {username}!\n\n"
                    f"🎉 У тебя есть бесплатный пробный период на {TRIAL_TIME} дней!\n"
                    "🕒 Не упусти возможность попробовать наш VPN прямо сейчас.\n\n"
                    "💡 Нажми на кнопку ниже, чтобы активировать пробный доступ."
                )

                await bot.send_message(tg_id, message, reply_markup=keyboard)
                logger.info(f"Отправлено уведомление неактивному пользователю {tg_id}.")

                await add_notification(tg_id, 'inactive_trial', session=conn)

        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления неактивному пользователю {tg_id}: {e}")

        await asyncio.sleep(1)


async def handle_expired_keys(bot: Bot, conn: asyncpg.Connection, current_time: float):
    logger.info("Проверка истекших ключей...")
    expiring_keys = await conn.fetch(
        """
        SELECT tg_id, client_id, expiry_time, email FROM keys 
        WHERE expiry_time <= $1
        """,
        current_time,
    )
    logger.info(f"current_time {current_time}")
    logger.info(f"Найдено {len(expiring_keys)} истекающих ключей.")

    await asyncio.gather(*[process_key(record, bot, conn) for record in expiring_keys])


async def process_key(record, bot, conn):
    tg_id = record["tg_id"]
    client_id = record["client_id"]
    email = record["email"]
    balance = await get_balance(tg_id)
    expiry_time = record["expiry_time"]
    expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
    current_date = datetime.utcnow()
    time_left = expiry_date - current_date

    logger.info(
        f"Время истечения ключа: {expiry_time} (дата: {expiry_date}), Текущее время: {current_date}, Оставшееся время: {time_left}"
    )
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")]]
    )

    try:
        if balance >= RENEWAL_PLANS["1"]["price"]:
            await update_balance(tg_id, -RENEWAL_PLANS["1"]["price"])
            new_expiry_time = int((datetime.utcnow() + timedelta(days=30)).timestamp() * 1000)
            await update_key_expiry(client_id, new_expiry_time)

            servers = await get_servers_from_db()

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
            logger.info(f"Флаги notified и notified_24 сброшены для клиента с ID {client_id}.")
            try:
                await bot.send_message(tg_id, text=KEY_RENEWED, reply_markup=keyboard)
                logger.info(f"Уведомление об успешном продлении отправлено клиенту {tg_id}.")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления клиенту {tg_id}: {e}")

        else:
            message_expired = "Ваша подписка истекла и была удалена. Получите новую через личный кабинет"

            try:
                await bot.send_message(tg_id, text=message_expired, reply_markup=keyboard)
                logger.info(f"Уведомление об истечении подписки и удалении ключа отправлено пользователю {tg_id}.")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления об истечении подписки пользователю {tg_id}: {e}")

            servers = await get_servers_from_db()

            for cluster_id in servers:
                await delete_key_from_cluster(cluster_id, email, client_id)
                logger.info(f"Клиент {client_id} удален из кластера {cluster_id}.")

            await delete_key(client_id)
            logger.info(f"Ключ для клиента с ID {client_id} удален из базы данных.")

    except Exception as e:
        logger.error(f"Ошибка при обработке ключа для клиента {tg_id}: {e}")


async def check_online_users():
    servers = await get_servers_from_db()

    for cluster_id, cluster in servers.items():
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
