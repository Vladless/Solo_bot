from datetime import datetime, timedelta

import asyncpg
from aiogram import Bot, Router, types
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (  # Импортируем необходимые классы
    InlineKeyboardButton, InlineKeyboardMarkup)
from aiogram.filters import Command

from bot import bot
from config import DATABASE_URL, ADMIN_PASSWORD, ADMIN_USERNAME, ADMIN_ID
from client import delete_client
from auth import login_with_credentials

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
                    [InlineKeyboardButton(text='3 месяца (285 руб.)', callback_data=f'renew_plan|3|{record["client_id"]}')],
                    [InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')]
                ])

                message = f"Ваш ключ <b>{email}</b> истечет и будет удален через <b>{hours_left} часов</b> (<b>{expiry_date}</b>). Пожалуйста, продлите его."

                try:
                    await bot.send_message(chat_id=tg_id, text=message, parse_mode='HTML', reply_markup=keyboard)
                except Exception as e:
                    print(f"Ошибка при отправке сообщения пользователю {tg_id}: {e}. Пропускаем этого пользователя.")

            # Обрабатываем истекшие ключи
            expired_records = await conn.fetch('''
                SELECT tg_id, email, client_id FROM keys 
                WHERE expiry_time <= $1
            ''', datetime.utcnow().timestamp() * 1000)

            for record in expired_records:
                tg_id = record['tg_id']
                email = record['email']
                client_id = record['client_id']

                # Удаляем ключ из базы данных
                await conn.execute('DELETE FROM keys WHERE client_id = $1', client_id)

                # Создаем сессию с использованием учетных данных
                session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)

                # Удаляем клиента из панели
                delete_client(session, client_id)

                # Создаем клавиатуру с кнопкой "В профиль"
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
    # Проверяем, является ли отправитель администратором
    if message.from_user.id != ADMIN_ID:  # Замените ADMIN_ID на ID вашего администратора
        await message.answer("У вас нет прав для выполнения этой команды.")
        return

    # Получаем текст сообщения
    text = message.get_args()
    if not text:
        await message.answer("Пожалуйста, введите текст сообщения после команды.")
        return

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # Получаем все tg_id клиентов
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