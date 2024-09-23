from datetime import datetime, timedelta

import asyncpg
from aiogram import Router, types

from auth import login_with_credentials
from bot import bot
from client import delete_client, extend_client_key
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL
from database import get_balance, update_balance

router = Router()

# Обработка запроса на просмотр ключей
@router.callback_query(lambda c: c.data == 'view_keys')
async def process_callback_view_keys(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            records = await conn.fetch('''
                SELECT email, client_id FROM keys WHERE tg_id = $1
            ''', tg_id)

            if records:
                # Создаем кнопки для каждого ключа
                buttons = []
                for record in records:
                    key_name = record['email']
                    client_id = record['client_id']
                    button = types.InlineKeyboardButton(text=key_name, callback_data=f'view_key_{key_name}_{client_id}')
                    buttons.append([button])

                # Создаем клавиатуру с кнопками
                inline_keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)
                response_message = "Выберите ключ для просмотра информации:"

                # Редактируем сообщение с клавиатурой
                await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=inline_keyboard)
            else:
                # Если нет ключей, добавляем кнопку "Создать ключ" и "Назад"
                response_message = "У вас нет ключей."

                # Кнопка "Создать ключ"
                create_key_button = types.InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
                back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_profile')  # Измените на правильное значение для кнопки "Назад"
                
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[create_key_button], [back_button]])

                await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)

        finally:
            await conn.close()

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при получении ключей: {e}")

    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('view_key_'))
async def process_callback_view_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    key_name, client_id = callback_query.data.split('_')[2], callback_query.data.split('_')[3]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('''
                SELECT k.key, k.expiry_time 
                FROM keys k
                WHERE k.tg_id = $1 AND k.email = $2
            ''', tg_id, key_name)

            if record:
                key = record['key']
                expiry_time = record['expiry_time']
                expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
                current_date = datetime.utcnow()
                days_left = (expiry_date - current_date).days

                days_left_message = f"Осталось дней: {days_left}" if days_left > 0 else "Ключ истек."
                response_message = (f"Ваш ключ:\n<pre>{key}</pre>\n"
                                    f"Дата окончания: <b>{expiry_date.strftime('%Y-%m-%d %H:%M:%S')}</b>\n"
                                    f"{days_left_message}")

                # Кнопки для продления, инструкций и удаления
                renew_button = types.InlineKeyboardButton(text='Продлить ключ', callback_data=f'renew_key_{client_id}')
                instructions_button = types.InlineKeyboardButton(text='Инструкции по использованию', callback_data='instructions')
                delete_button = types.InlineKeyboardButton(text='Удалить ключ', callback_data=f'delete_key_{client_id}')
                back_button = types.InlineKeyboardButton(text='Назад в профиль', callback_data='view_profile')
                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[renew_button], [instructions_button], [delete_button], [back_button]])

                await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard, parse_mode="HTML")
            else:
                await bot.edit_message_text("Информация о ключе не найдена.", chat_id=tg_id, message_id=callback_query.message.message_id, parse_mode="HTML")

        finally:
            await conn.close()

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при получении информации о ключе: {e}")

    await callback_query.answer()

# Обработка запроса на удаление ключа
@router.callback_query(lambda c: c.data.startswith('delete_key_'))
async def process_callback_delete_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split('_')[2]

    confirmation_keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text='Да, удалить', callback_data=f'confirm_delete_{client_id}')],
        [types.InlineKeyboardButton(text='Нет, отменить', callback_data='view_keys')]
    ])

    await bot.edit_message_text("Вы уверены, что хотите удалить ключ?", chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=confirmation_keyboard)
    await callback_query.answer()

@router.callback_query(lambda c: c.data.startswith('confirm_delete_'))
async def process_callback_confirm_delete(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split('_')[2]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('SELECT email FROM keys WHERE client_id = $1', client_id)

            if record:
                email = record['email']
                
                session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)
                success = delete_client(session, client_id)

                if success:
                    await conn.execute('DELETE FROM keys WHERE client_id = $1', client_id)
                    response_message = "Ключ был успешно удален."
                else:
                    response_message = "Ошибка при удалении клиента через API."

            else:
                response_message = "Ключ не найден или уже удален."

            back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_keys')
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])

            await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(f"Ошибка при удалении ключа: {e}", chat_id=tg_id, message_id=callback_query.message.message_id)

    await callback_query.answer()


@router.callback_query(lambda c: c.data.startswith('renew_key_'))
async def process_callback_renew_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    client_id = callback_query.data.split('_')[2]

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('SELECT email, expiry_time FROM keys WHERE client_id = $1', client_id)

            if record:
                email = record['email']
                expiry_time = record['expiry_time']
                current_time = datetime.utcnow().timestamp() * 1000

                if expiry_time <= current_time:
                    # Кнопка для удаления ключа и возврата в профиль
                    delete_button = types.InlineKeyboardButton(text='Удалить ключ', callback_data=f'delete_key_{client_id}')
                    back_button = types.InlineKeyboardButton(text='Назад в профиль', callback_data='view_profile')
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[delete_button], [back_button]])

                    await bot.edit_message_text(
                        "Ваш ключ уже истек и не может быть продлен.",
                        chat_id=tg_id,
                        message_id=callback_query.message.message_id,
                        reply_markup=keyboard
                    )
                    return

                keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text='Продлить на 1 месяц (100 руб.)', callback_data=f'renew_1_month_{client_id}')],
                    [types.InlineKeyboardButton(text='Продлить на 3 месяца (250 руб.)', callback_data=f'renew_3_months_{client_id}')],
                    [types.InlineKeyboardButton(text='Назад', callback_data='view_profile')]
                ])

                balance = await get_balance(tg_id)
                response_message = (f"Выберите план продления:\n"
                                    f"Баланс: <b>{balance} руб.</b>\n"
                                    f"Действующий ключ истекает <b>{datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')}</b>")

                await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard, parse_mode="HTML")

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(f"Ошибка при выборе плана: {e}", chat_id=tg_id, message_id=callback_query.message.message_id)

    await callback_query.answer()

# Обработка выбора плана продления
@router.callback_query(lambda c: c.data.startswith('renew_'))
async def process_callback_renew_plan(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    plan, client_id = callback_query.data.split('_')[1], callback_query.data.split('_')[3]  # '1' или '3' и client_id
    days_to_extend = 30 * int(plan)

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            record = await conn.fetchrow('SELECT email, expiry_time FROM keys WHERE client_id = $1', client_id)

            if record:
                email = record['email']
                expiry_time = record['expiry_time']
                current_time = datetime.utcnow().timestamp() * 1000

                if expiry_time <= current_time:
                    await bot.edit_message_text("Ваш ключ уже истек и не может быть продлен.", chat_id=tg_id, message_id=callback_query.message.message_id)
                    return

                new_expiry_time = int(expiry_time + timedelta(days=days_to_extend).total_seconds() * 1000)
                cost = 100 if plan == '1' else 250

                balance = await get_balance(tg_id)
                if balance < cost:
                    replenish_button = types.InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')
                    back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_keys')
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[replenish_button], [back_button]])

                    await bot.edit_message_text("Недостаточно средств для продления ключа.", chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)
                    return

                session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)
                success = extend_client_key(session, tg_id, client_id, email, new_expiry_time)

                if success:
                    await update_balance(tg_id, -cost)
                    await conn.execute('UPDATE keys SET expiry_time = $1 WHERE client_id = $2', new_expiry_time, client_id)
                    response_message = f"Ваш ключ был успешно продлен на {days_to_extend // 30} месяц(-)."
                    back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_keys')
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])
                    await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)
                else:
                    await bot.edit_message_text("Ошибка при продлении ключа.", chat_id=tg_id, message_id=callback_query.message.message_id)
            else:
                await bot.edit_message_text("У вас нет ключей для продления.", chat_id=tg_id, message_id=callback_query.message.message_id)

        finally:
            await conn.close()

    except Exception as e:
        await bot.edit_message_text(f"Ошибка при продлении ключа: {e}", chat_id=tg_id, message_id=callback_query.message.message_id)

    await callback_query.answer()

# Обработка ошибок
async def handle_error(tg_id, callback_query, message):
    await bot.edit_message_text(message, chat_id=tg_id, message_id=callback_query.message.message_id)

