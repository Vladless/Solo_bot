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
            threshold_time = (datetime.utcnow() + timedelta(hours=10)).timestamp() * 1000 
            records = await conn.fetch('''
                SELECT tg_id, email, expiry_time, client_id, server_id FROM keys 
                WHERE expiry_time <= $1 AND expiry_time > $2 AND notified = FALSE
            ''', threshold_time, datetime.utcnow().timestamp() * 1000)

            for record in records:
                tg_id = record['tg_id']
                email = record['email']
                expiry_time = record['expiry_time']
                server_id = record['server_id']

                time_left = (expiry_time / 1000) - datetime.utcnow().timestamp()
                hours_left = max(0, int(time_left // 3600))

                expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
                balance = await get_balance(tg_id) 

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text='1 месяц (100 руб.)', callback_data=f'renew_plan|1|{record["client_id"]}'),
                        InlineKeyboardButton(text='3 месяца (285 руб.)', callback_data=f'renew_plan|3|{record["client_id"]}')
                    ],
                    [
                        InlineKeyboardButton(text='6 месяцев (540 руб.)', callback_data=f'renew_plan|6|{record["client_id"]}'),
                        InlineKeyboardButton(text='1 год (1000 руб.)', callback_data=f'renew_plan|12|{record["client_id"]}')
                    ],
                    [
                        InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance'),
                        InlineKeyboardButton(text='Назад', callback_data='back_to_main')
                    ]
                ])

                message = (f"Ваш ключ <b>{email}</b> истечет и будет удален через <b>{hours_left} часов</b> "
                           f"(<b>{expiry_date}</b>). Пожалуйста, продлите его.\n"
                           f"Ваш текущий баланс: <b>{balance:.2f} руб.</b>")

                try:
                    await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML', reply_markup=keyboard)

                    await conn.execute('UPDATE keys SET notified = TRUE WHERE client_id = $1', record['client_id'])
                except Exception as e:
                    print(f"Ошибка при отправке сообщения пользователю {tg_id}: {e}. Пропускаем этого пользователя.")

            expired_records = await conn.fetch('''
                SELECT tg_id, email, client_id, server_id FROM keys 
                WHERE expiry_time <= $1
            ''', datetime.utcnow().timestamp() * 1000)

            for record in expired_records:
                tg_id = record['tg_id']
                email = record['email']
                client_id = record['client_id']
                server_id = record['server_id']

                balance = await get_balance(tg_id)

                if balance >= 100:
                    # Продлеваем ключ на 1 месяц и списываем 100 рублей
                    new_expiry_time = datetime.utcnow() + timedelta(days=30)
                    await conn.execute('''
                        UPDATE keys SET expiry_time = $1 WHERE client_id = $2
                    ''', new_expiry_time.timestamp() * 1000, client_id)

                    # Списываем 100 рублей с баланса
                    await update_balance(tg_id, -100)

                    session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                    await extend_client_key(session, server_id, client_id, 30)

                    message = (f"Ваш ключ <b>{email}</b> был автоматически продлен на 1 месяц, так как у вас на балансе было достаточно средств. "
                               f"Новый срок истечения: {new_expiry_time.strftime('%Y-%m-%d %H:%M:%S')}.\n"
                               f"Ваш новый баланс: <b>{balance - 100:.2f} руб.</b>")

                    try:
                        await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML')
                    except Exception as e:
                        print(f"Ошибка при отправке сообщения пользователю {tg_id}: {e}. Пропускаем этого пользователя.")
                else:
                    # Удаляем ключ, если баланс недостаточный
                    await conn.execute('DELETE FROM keys WHERE client_id = $1', client_id)

                    session = await login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                    await delete_client(session, server_id, client_id)

                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='В профиль', callback_data='view_profile')]
                    ])

                    message = f"Ваш ключ <b>{email}</b> истек, и был удален."

                    try:
                        await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML', reply_markup=keyboard)
                    except Exception as e:
                        print(f"Ошибка при отправке сообщения пользователю {tg_id}: {e}. Пропускаем этого пользователя.")

        finally:
            await conn.close()
    except Exception as e:
        print(f"Ошибка при отправке уведомлений: {e}")