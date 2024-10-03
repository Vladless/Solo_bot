from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Router, types
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from auth import login_with_credentials
from bot import bot
from client import delete_client
from config import ADMIN_ID, ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL
from database import get_balance

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
                hours_left = max(0, int(time_left // 3600) - 3)

                expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')
                balance = await get_balance(tg_id) 

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text='1 месяц (100 руб.)', callback_data=f'renew_plan|1|{record["client_id"]}'),
                        InlineKeyboardButton(text='3 месяца (285 руб.)', callback_data=f'renew_plan|3|{record["client_id"]}')
                    ],
                    [
                        InlineKeyboardButton(text='6 месяцев (540 руб.)', callback_data=f'renew_plan|6|{record["client_id"]}'),
                        InlineKeyboardButton(text='1 год (1080 руб.)', callback_data=f'renew_plan|12|{record["client_id"]}')
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

                await conn.execute('DELETE FROM keys WHERE client_id = $1', client_id)

                session = login_with_credentials(server_id, ADMIN_USERNAME, ADMIN_PASSWORD)
                delete_client(session, server_id, client_id)

                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text='В профиль', callback_data='view_profile')]
                ])

                message = f"Ваш ключ <b>{email}</b> истек и был удален автоматически."

                try:
                    await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML', reply_markup=keyboard)
                except Exception as e:
                    print(f"Ошибка при отправке сообщения пользователю {tg_id}: {e}. Пропускаем этого пользователя.")

        finally:
            await conn.close()
    except Exception as e:
        print(f"Ошибка при отправке уведомлений: {e}")


@router.message(Command('send_to_all'))
async def send_message_to_all_clients(message: types.Message):
    if message.from_user.id != ADMIN_ID: 
        await message.answer("У вас нет прав для выполнения этой команды.")
        return

    text = message.get_args()
    if not text:
        await message.answer("Пожалуйста, введите текст сообщения после команды.")
        return

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        tg_ids = await conn.fetch('SELECT tg_id FROM keys')

        for record in tg_ids:
            tg_id = record['tg_id']
            try:
                await bot.send_message(chat_id=tg_id, text=text)
            except Exception as e:
                print(f"Ошибка при отправке сообщения пользователю {tg_id}: {e}. Пропускаем этого пользователя.")

        await message.answer("Сообщение было отправлено всем клиентам.")
    except Exception as e:
        print(f"Ошибка при подключении к базе данных: {e}")
        await message.answer("Произошла ошибка при отправке сообщения.")
    finally:
        await conn.close()
