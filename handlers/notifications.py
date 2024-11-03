from datetime import datetime, timedelta
import asyncpg
import asyncio
from aiogram import Bot, Router
from aiogram.fsm.state import State, StatesGroup
import logging
from config import DATABASE_URL, ADMIN_USERNAME, ADMIN_PASSWORD, SERVERS
from database import get_balance, update_key_expiry, delete_key, update_balance
from client import extend_client_key, delete_client
from auth import login_with_credentials
from handlers.texts import KEY_EXPIRY_10H, KEY_EXPIRY_24H, KEY_RENEWED, KEY_RENEWAL_FAILED
from aiogram import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = Router()

class NotificationStates(StatesGroup):
    waiting_for_notification_text = State()

async def notify_expiring_keys(bot: Bot):
    conn = None
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        logger.info("Подключение к базе данных успешно.")
        
        current_time = datetime.utcnow().timestamp() * 1000
        threshold_time_10h = (datetime.utcnow() + timedelta(hours=10)).timestamp() * 1000
        threshold_time_24h = (datetime.utcnow() + timedelta(days=1)).timestamp() * 1000 

        logger.info("Начало обработки уведомлений.")

        await notify_10h_keys(bot, conn, current_time, threshold_time_10h)
        await asyncio.sleep(1) 
        await notify_24h_keys(bot, conn, current_time, threshold_time_24h)
        await asyncio.sleep(1) 
        await handle_expired_keys(bot, conn, current_time)

    except Exception as e:
        logger.error(f"Ошибка при отправке уведомлений: {e}")
    finally:
        if conn:
            await conn.close()
            logger.info("Соединение с базой данных закрыто.")

async def is_bot_blocked(bot: Bot, chat_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, bot.id)
        return member.status == 'left' 
    except Exception as e:
        logger.error(f"Ошибка при проверке статуса бота у пользователя {chat_id}: {e}")
        return False 

async def notify_10h_keys(bot: Bot, conn: asyncpg.Connection, current_time: float, threshold_time_10h: float):
    records = await conn.fetch('''
        SELECT tg_id, email, expiry_time, client_id, server_id FROM keys 
        WHERE expiry_time <= $1 AND expiry_time > $2 AND notified = FALSE
    ''', threshold_time_10h, current_time)

    logger.info(f"Найдено {len(records)} ключей для уведомления за 10 часов.")
    for record in records:
        tg_id = record['tg_id']
        email = record['email']
        expiry_time = record['expiry_time']
        server_id = record['server_id']

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

        server_name = SERVERS[server_id]['name']
        message = KEY_EXPIRY_10H.format(
            server_id=server_name, 
            email=email, 
            expiry_date=expiry_date.strftime('%Y-%m-%d %H:%M:%S'),
            days_left_message=days_left_message
        )

        if not await is_bot_blocked(bot, tg_id):
            try:
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔄 Продлить VPN', callback_data=f'renew_key|{record["client_id"]}')],
                    [types.InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='replenish_balance')],
                    [types.InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')]
                ])
                await bot.send_message(tg_id, message, reply_markup=keyboard)
                logger.info(f"Уведомление отправлено пользователю {tg_id}.")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления пользователю {tg_id}: {e}")
                continue  

            await conn.execute('UPDATE keys SET notified = TRUE WHERE client_id = $1', record['client_id'])
            logger.info(f"Обновлено поле notified для клиента {record['client_id']}.")
        
        await asyncio.sleep(1)  

async def notify_24h_keys(bot: Bot, conn: asyncpg.Connection, current_time: float, threshold_time_24h: float):
    logger.info("Проверка истекших ключей...")

    records_24h = await conn.fetch('''
        SELECT tg_id, email, expiry_time, client_id, server_id FROM keys 
        WHERE expiry_time <= $1 AND expiry_time > $2 AND notified_24h = FALSE
    ''', threshold_time_24h, current_time)

    logger.info(f"Найдено {len(records_24h)} ключей для уведомления за 24 часа.")
    for record in records_24h:
        tg_id = record['tg_id']
        email = record['email']
        expiry_time = record['expiry_time']
        server_id = record['server_id']

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
            server_id=SERVERS[server_id]['name'],
            email=email,
            days_left_message=days_left_message,
            expiry_date=expiry_date.strftime('%Y-%m-%d %H:%M:%S')
        )

        if not await is_bot_blocked(bot, tg_id):
            try:
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text='🔄 Продлить VPN', callback_data=f'renew_key|{record["client_id"]}')],
                    [types.InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='replenish_balance')],
                    [types.InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')]
                ])
                await bot.send_message(tg_id, message_24h, reply_markup=keyboard)
                logger.info(f"Уведомление за 24 часа отправлено пользователю {tg_id}.")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления за 24 часа пользователю {tg_id}: {e}")
                continue  

            await conn.execute('UPDATE keys SET notified_24h = TRUE WHERE client_id = $1', record['client_id'])
            logger.info(f"Обновлено поле notified_24h для клиента {record['client_id']}.")
        
        await asyncio.sleep(1)  


async def handle_expired_keys(bot: Bot, conn: asyncpg.Connection, current_time: float):
    logger.info("Проверка истекших ключей...")

    current_time = datetime.utcnow().timestamp() * 1000
    adjusted_current_time = current_time + (3 * 60 * 60 * 1000)

    logger.info(f"Текущее время: {current_time}, Скорректированное текущее время: {adjusted_current_time}")

    expiring_keys = await conn.fetch('''
        SELECT tg_id, client_id, expiry_time, server_id, email FROM keys 
        WHERE expiry_time <= $1
    ''', adjusted_current_time)

    logger.info(f"Найдено {len(expiring_keys)} истекающих ключей.")

    for record in expiring_keys:
        tg_id = record['tg_id']
        client_id = record['client_id']
        balance = await get_balance(tg_id)
        server_id = record['server_id']
        email = record['email']

        logger.info(f"Проверка баланса для клиента {tg_id}: {balance}.")
        
        expiry_time = record['expiry_time']
        expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
        current_date = datetime.utcnow()
        time_left = expiry_date - current_date
        logger.info(f"Время истечения ключа: {expiry_time} (дата: {expiry_date}), Текущее время: {current_date}, Оставшееся время: {time_left}.")

        if time_left.total_seconds() <= 0:
            days_left_message = "Ключ истек"
        elif time_left.days > 0:
            days_left_message = f"Осталось дней: <b>{time_left.days}</b>"
        else:
            hours_left = time_left.seconds // 3600
            days_left_message = f"Осталось часов: <b>{hours_left}</b>"

        message_expired = f"Ваш ключ {email} для сервера {SERVERS[server_id]['name']} истек и был удален!\n\n Перейдите в профиль для создания нового ключа"

        button_profile = types.InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[button_profile]])

        if balance >= 100:
            await update_balance(tg_id, -100)
            new_expiry_time = int((datetime.utcnow() + timedelta(days=30)).timestamp() * 1000)
            await update_key_expiry(client_id, new_expiry_time)
            logger.info(f"Ключ для клиента {tg_id} продлен до {datetime.utcfromtimestamp(new_expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')}.")

            session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
            success = await extend_client_key(session, server_id, tg_id, client_id, email, new_expiry_time)
            if success:
                try:
                    await bot.send_message(tg_id, KEY_RENEWED, reply_markup=keyboard)
                    logger.info(f"Ключ для пользователя {tg_id} успешно продлен на месяц.")
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления о продлении ключа пользователю {tg_id}: {e}")
            else:
                try:
                    await bot.send_message(tg_id, KEY_RENEWAL_FAILED, reply_markup=keyboard)
                    logger.error(f"Не удалось продлить ключ для пользователя {tg_id}.")
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления о неудачном продлении ключа пользователю {tg_id}: {e}")
        else:
            try:
                await bot.send_message(tg_id, message_expired, reply_markup=keyboard)
                await delete_key(client_id)
                session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                success = await delete_client(session, server_id, client_id)
                logger.info(f"Ключ для клиента {tg_id} удален из базы данных.")
            except Exception as e:
                logger.error(f"Ошибка при удалении ключа для клиента {tg_id}: {e}")
        
        await asyncio.sleep(1)  
