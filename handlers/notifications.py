import asyncio
from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Router, types
from loguru import logger
from py3xui import AsyncApi

from client import delete_client, extend_client_key
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL, SERVERS
from database import delete_key, get_balance, update_balance, update_key_expiry
from handlers.texts import KEY_EXPIRY_10H, KEY_EXPIRY_24H, KEY_RENEWED, RENEWAL_PLANS

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
                                text="👤 Мой профиль", callback_data="view_profile"
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
                                text="👤 Мой профиль", callback_data="view_profile"
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

    current_time = datetime.utcnow().timestamp() * 1000
    adjusted_current_time = current_time + (3 * 60 * 60 * 1000)

    logger.info(
        f"Текущее время: {current_time}, Скорректированное текущее время: {adjusted_current_time}"
    )

    expiring_keys = await conn.fetch(
        """
        SELECT tg_id, client_id, expiry_time, email FROM keys 
        WHERE expiry_time <= $1
        """,
        adjusted_current_time,
    )

    logger.info(f"Найдено {len(expiring_keys)} истекающих ключей.")

    for record in expiring_keys:
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

        message_expired = f"Ваша подписка {email} истекла и была удалена!\n\n Перейдите в профиль для создания нового ключа"
        button_profile = types.InlineKeyboardButton(
            text="👤 Мой профиль", callback_data="view_profile"
        )
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[button_profile]])

        try:
            if balance >= RENEWAL_PLANS["1"]["price"]:
                await update_balance(tg_id, -RENEWAL_PLANS["1"]["price"])
                new_expiry_time = int(
                    (datetime.utcnow() + timedelta(days=30)).timestamp() * 1000
                )
                await update_key_expiry(client_id, new_expiry_time)
                logger.info(
                    f"Ключ для клиента {tg_id} продлен до {datetime.utcfromtimestamp(new_expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')}."
                )

                all_success = True
                for server_id in SERVERS:
                    xui = AsyncApi(
                        SERVERS[server_id]["API_URL"],
                        username=ADMIN_USERNAME,
                        password=ADMIN_PASSWORD,
                    )
                    success = await extend_client_key(
                        xui, email, new_expiry_time, client_id
                    )
                    if not success:
                        all_success = False
                        logger.error(
                            f"Не удалось продлить ключ для пользователя {tg_id} на сервере {server_id}."
                        )

                if all_success:
                    try:
                        await bot.send_message(
                            tg_id, KEY_RENEWED, reply_markup=keyboard
                        )
                        logger.info(
                            f"Ключ для пользователя {tg_id} успешно продлен на месяц на всех серверах."
                        )
                    except Exception as e:
                        logger.error(
                            f"Ошибка при отправке уведомления о продлении ключа пользователю {tg_id}: {e}"
                        )
            else:
                try:
                    await bot.send_message(
                        tg_id, message_expired, reply_markup=keyboard
                    )
                    await delete_key(client_id)

                    for server_id in SERVERS:
                        xui = AsyncApi(
                            SERVERS[server_id]["API_URL"],
                            username=ADMIN_USERNAME,
                            password=ADMIN_PASSWORD,
                        )
                        success = await delete_client(xui, email, client_id)
                        if success:
                            logger.info(
                                f"Ключ для клиента {tg_id} успешно удален с сервера {server_id}."
                            )
                        else:
                            logger.error(
                                f"Не удалось удалить ключ для клиента {tg_id} на сервере {server_id}."
                            )
                except Exception as e:
                    logger.error(f"Ошибка при удалении ключа для клиента {tg_id}: {e}")

        except Exception as e:
            logger.error(f"Ошибка при обработке ключа для клиента {tg_id}: {e}")

        await asyncio.sleep(1)
