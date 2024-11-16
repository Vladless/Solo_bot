import asyncio
from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Router, types
from py3xui import AsyncApi

from client import delete_client
from config import ADMIN_PASSWORD, ADMIN_USERNAME, CLUSTERS, DATABASE_URL, TOTAL_GB
from database import delete_key, get_balance, update_balance, update_key_expiry
from handlers.keys.key_utils import renew_key_in_cluster
from handlers.texts import KEY_EXPIRY_10H, KEY_EXPIRY_24H, KEY_RENEWED, RENEWAL_PLANS
from logger import logger

router = Router()


async def notify_expiring_keys(bot: Bot):
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info("Подключение к базе данных успешно.")

        current_time = datetime.utcnow().timestamp() * 1000
        threshold_time_10h = (
            datetime.utcnow() + timedelta(hours=10)
        ).timestamp() * 1000
        threshold_time_24h = (datetime.utcnow() + timedelta(days=1)).timestamp() * 1000

        logger.info("Начало обработки уведомлений.")

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
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        blocked = member.status == "left"
        logger.info(
            f"Статус бота для пользователя {chat_id}: {'заблокирован' if blocked else 'активен'}"
        )
        return blocked
    except Exception as e:
        logger.warning(
            f"Не удалось проверить статус бота для пользователя {chat_id}: {e}"
        )
        return False


async def notify_10h_keys(
    bot: Bot, conn: asyncpg.Connection, current_time: float, threshold_time_10h: float
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
        )

        if not await is_bot_blocked(bot, tg_id):
            try:
                keyboard = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text="🔄 Продлить VPN",
                                callback_data=f'renew_key|{record["client_id"]}',
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="💳 Пополнить баланс",
                                callback_data="pay",
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="👤 Личный кабинет", callback_data="view_profile"
                            )
                        ],
                    ]
                )
                await bot.send_message(tg_id, message, reply_markup=keyboard)
                logger.info(f"Уведомление отправлено пользователю {tg_id}.")
            except Exception as e:
                logger.error(
                    f"Ошибка при отправке уведомления пользователю {tg_id}: {e}"
                )
                continue

            await conn.execute(
                "UPDATE keys SET notified = TRUE WHERE client_id = $1",
                record["client_id"],
            )
            logger.info(f"Обновлено поле notified для клиента {record['client_id']}.")

        await asyncio.sleep(1)


async def notify_24h_keys(
    bot: Bot, conn: asyncpg.Connection, current_time: float, threshold_time_24h: float
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

        if not await is_bot_blocked(bot, tg_id):
            try:
                keyboard = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            types.InlineKeyboardButton(
                                text="🔄 Продлить VPN",
                                callback_data=f'renew_key|{record["client_id"]}',
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="💳 Пополнить баланс",
                                callback_data="pay",
                            )
                        ],
                        [
                            types.InlineKeyboardButton(
                                text="👤 Личный кабинет", callback_data="view_profile"
                            )
                        ],
                    ]
                )
                await bot.send_message(tg_id, message_24h, reply_markup=keyboard)
                logger.info(f"Уведомление за 24 часа отправлено пользователю {tg_id}.")
            except Exception as e:
                logger.error(
                    f"Ошибка при отправке уведомления за 24 часа пользователю {tg_id}: {e}"
                )
                continue

            await conn.execute(
                "UPDATE keys SET notified_24h = TRUE WHERE client_id = $1",
                record["client_id"],
            )
            logger.info(
                f"Обновлено поле notified_24h для клиента {record['client_id']}."
            )

        await asyncio.sleep(1)


async def handle_expired_keys(bot: Bot, conn: asyncpg.Connection, current_time: float):
    logger.info("Проверка истекших ключей...")

    adjusted_current_time = current_time + (3 * 60 * 60 * 1000)
    expiring_keys = await conn.fetch(
        """
        SELECT tg_id, client_id, expiry_time, email FROM keys 
        WHERE expiry_time <= $1
        """,
        adjusted_current_time,
    )
    logger.info(f"Найдено {len(expiring_keys)} истекающих ключей.")

    async def process_key(record):
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

        message_expired = (
            f"❌ Ваша подписка {email} истекла и была удалена!\n\n"
            "🔍 Перейдите в профиль для создания новой подписки.\n"
            "💡 Не откладывайте подключение VPN!"
        )
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="👤 Личный кабинет", callback_data="view_profile"
                    )
                ]
            ]
        )

        try:
            if balance >= RENEWAL_PLANS["1"]["price"]:
                await update_balance(tg_id, -RENEWAL_PLANS["1"]["price"])
                new_expiry_time = int(
                    (datetime.utcnow() + timedelta(days=30)).timestamp() * 1000
                )
                await update_key_expiry(client_id, new_expiry_time)

                for cluster_id in CLUSTERS:
                    await renew_key_in_cluster(
                        cluster_id, email, client_id, new_expiry_time, TOTAL_GB
                    )
                    logger.info(
                        f"Ключ для пользователя {tg_id} успешно продлен в кластере {cluster_id}."
                    )

                await conn.execute(
                    """
                    UPDATE keys
                    SET notified = FALSE, notified_24h = FALSE
                    WHERE client_id = $1
                    """,
                    client_id,
                )
                logger.info(
                    f"Флаги notified и notified_24 сброшены для клиента с ID {client_id}."
                )
                try:
                    await bot.send_message(
                        tg_id, text=KEY_RENEWED, reply_markup=keyboard
                    )
                    logger.info(
                        f"Уведомление об успешном продлении отправлено клиенту {tg_id}."
                    )
                except Exception as e:
                    logger.error(
                        f"Ошибка при отправке уведомления клиенту {tg_id}: {e}"
                    )

            else:
                await safe_send_message(
                    bot, tg_id, message_expired, reply_markup=keyboard
                )
                await delete_key(client_id)

                for cluster_id, cluster in CLUSTERS.items():
                    for server_id, server in cluster.items():
                        xui = AsyncApi(
                            server["API_URL"],
                            username=ADMIN_USERNAME,
                            password=ADMIN_PASSWORD,
                        )
                        await delete_client(xui, email, client_id)

        except Exception as e:
            logger.error(f"Ошибка при обработке ключа для клиента {tg_id}: {e}")

    await asyncio.gather(*[process_key(record) for record in expiring_keys])


async def safe_send_message(bot, tg_id, text, reply_markup=None):
    try:
        await bot.send_message(tg_id, text, reply_markup=reply_markup)
    except Exception as e:
        if "chat not found" in str(e):
            logger.warning(f"Чат для клиента {tg_id} не найден.")
        else:
            logger.error(f"Ошибка при отправке сообщения клиенту {tg_id}: {e}")
