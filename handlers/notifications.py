from datetime import datetime, timedelta
import asyncpg
from aiogram import Bot, Router, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from auth import login_with_credentials
from bot import bot
from client import delete_client, extend_client_key
from config import ADMIN_ID, ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL
from database import get_balance, update_balance

router = Router()

class NotificationStates(StatesGroup):
    waiting_for_notification_text = State()

async def notify_expiring_keys(bot: Bot):
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            current_time = datetime.utcnow().timestamp() * 1000 
            threshold_time_10h = (datetime.utcnow() + timedelta(hours=10)).timestamp() * 1000
            threshold_time_24h = (datetime.utcnow() + timedelta(days=1)).timestamp() * 1000 

            # Уведомления за 10 часов
            records = await conn.fetch('''
                SELECT tg_id, email, expiry_time, client_id, server_id FROM keys 
                WHERE expiry_time <= $1 AND expiry_time > $2 AND notified = FALSE
            ''', threshold_time_10h, current_time)

            for record in records:
                tg_id = record['tg_id']
                email = record['email']
                expiry_time = record['expiry_time']
                server_id = record['server_id']

                # Логика формирования и отправки сообщения для уведомлений за 10 часов
                message = f"🔔 Уведомление: Ваш ключ для сервера {server_id} истекает через 10 часов.\n" \
                          f"Email: {email}\n" \
                          f"Дата истечения: {datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')}"
                
                await bot.send_message(tg_id, message)

                # Обновление статуса уведомления
                await conn.execute('UPDATE keys SET notified = TRUE WHERE client_id = $1', record['client_id'])

            # Уведомления за 24 часа
            records_24h = await conn.fetch('''
                SELECT tg_id, email, expiry_time, client_id, server_id FROM keys 
                WHERE expiry_time <= $1 AND expiry_time > $2 AND notified_24h = FALSE
            ''', threshold_time_24h, current_time)

            for record in records_24h:
                tg_id = record['tg_id']
                email = record['email']
                expiry_time = record['expiry_time']
                server_id = record['server_id']

                time_left = (expiry_time / 1000) - datetime.utcnow().timestamp()
                hours_left = max(0, int(time_left // 3600))

                expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
                balance = await get_balance(tg_id)

                # Логика формирования и отправки сообщения для уведомлений за 24 часа
                message_24h = f"⏳ Уведомление: Ваш ключ для сервера {server_id} истекает через 24 часа.\n" \
                               f"Email: {email}\n" \
                               f"Осталось времени: {hours_left} часов\n" \
                               f"Дата истечения: {expiry_date}\n" \
                               f"Баланс: {balance:.2f} руб."

                await bot.send_message(tg_id, message_24h)

                # Обновление статуса уведомления за 24 часа
                await conn.execute('UPDATE keys SET notified_24h = TRUE WHERE client_id = $1', record['client_id'])

        finally:
            await conn.close()
    except Exception as e:
        print(f"Ошибка при отправке уведомлений: {e}")
