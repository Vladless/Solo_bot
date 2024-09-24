import asyncpg
from datetime import datetime, timedelta
from aiogram import Bot

from config import DATABASE_URL

async def notify_expiring_keys(bot: Bot):
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # Получаем все ключи, которые истекают в течение следующих 10 часов
            threshold_time = (datetime.utcnow() + timedelta(hours=10)).timestamp() * 1000  # В миллисекундах
            records = await conn.fetch('''
                SELECT tg_id, email, expiry_time FROM keys 
                WHERE expiry_time <= $1 AND expiry_time > $2
            ''', threshold_time, datetime.utcnow().timestamp() * 1000)

            for record in records:
                tg_id = record['tg_id']
                email = record['email']
                expiry_time = record['expiry_time']
                expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
                message = f"Ваш ключ <b>{email}</b> истечет <b>{expiry_date}</b>. Пожалуйста, продлите его."
                await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML')

            # Получаем все истекшие ключи
            expired_records = await conn.fetch('''
                SELECT tg_id, email FROM keys 
                WHERE expiry_time <= $1
            ''', datetime.utcnow().timestamp() * 1000)

            for record in expired_records:
                tg_id = record['tg_id']
                email = record['email']
                message = f"Ваш ключ <b>{email}</b> уже истек. Пожалуйста, продлите его."
                await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML')

        finally:
            await conn.close()
    except Exception as e:
        print(f"Ошибка при отправке уведомлений: {e}")
