from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Router
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (  # Импортируем необходимые классы
    InlineKeyboardButton, InlineKeyboardMarkup)

from bot import bot
from config import DATABASE_URL

router = Router()

class NotificationStates(StatesGroup):
    waiting_for_notification_text = State()

async def notify_expiring_keys(bot: Bot):
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # Получаем все ключи, которые истекают в течение следующих 10 часов
            threshold_time = (datetime.utcnow() + timedelta(hours=10)).timestamp() * 1000  # В миллисекундах
            records = await conn.fetch('''
                SELECT tg_id, email, expiry_time, client_id FROM keys 
                WHERE expiry_time <= $1 AND expiry_time > $2
            ''', threshold_time, datetime.utcnow().timestamp() * 1000)

            for record in records:
                tg_id = record['tg_id']
                email = record['email']
                expiry_time = record['expiry_time']

                # Рассчитываем оставшееся время и уменьшаем его на 3 часа
                time_left = (expiry_time / 1000) - datetime.utcnow().timestamp()
                hours_left = max(0, int(time_left // 3600) - 3)

                expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')

                # Создаем клавиатуру с выбором тарифов
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='1 месяц (100 руб.)', callback_data=f'renew_plan|1|{record["client_id"]}')],
                    [InlineKeyboardButton(text='3 месяца (250 руб.)', callback_data=f'renew_plan|3|{record["client_id"]}')],
                    [InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')]
                ])

                message = f"Ваш ключ <b>{email}</b> истечет через <b>{hours_left} часов</b> (<b>{expiry_date}</b>). Пожалуйста, продлите его."
                await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML', reply_markup=keyboard)

            # Обрабатываем истекшие ключи
            expired_records = await conn.fetch('''
                SELECT tg_id, email FROM keys 
                WHERE expiry_time <= $1
            ''', datetime.utcnow().timestamp() * 1000)

            for record in expired_records:
                tg_id = record['tg_id']
                email = record['email']

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='1 месяц (100 руб.)', callback_data=f'renew_plan|1|{record["client_id"]}')],
                    [InlineKeyboardButton(text='3 месяца (250 руб.)', callback_data=f'renew_plan|3|{record["client_id"]}')],
                    [InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')]
                ])

                message = f"Ваш ключ <b>{email}</b> уже истек. Пожалуйста, продлите его."
                await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML', reply_markup=keyboard)

        finally:
            await conn.close()
    except Exception as e:
        print(f"Ошибка при отправке уведомлений: {e}")
