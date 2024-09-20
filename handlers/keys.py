from aiogram import types, Router
import aiosqlite
from bot import bot
from datetime import datetime, timedelta
from database import get_balance, update_balance
from client import extend_client_key, login_with_credentials
from config import ADMIN_USERNAME, DATABASE_PATH, ADMIN_PASSWORD

router = Router()

# Обработка запроса на просмотр ключей
@router.callback_query(lambda c: c.data == 'view_keys')
async def process_callback_view_keys(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id

    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute('''
                SELECT email FROM connections WHERE tg_id = ?
            ''', (tg_id,)) as cursor:
                records = await cursor.fetchall()

                if records:
                    # Создаем кнопки для каждого ключа
                    buttons = []
                    for record in records:
                        key_name = record[0]  # Предполагается, что email - это название ключа
                        button = types.InlineKeyboardButton(text=key_name, callback_data=f'view_key_{key_name}')
                        buttons.append([button])

                    # Создаем клавиатуру с кнопками
                    inline_keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)
                    response_message = "Выберите ключ для просмотра информации:"
                    
                    # Редактируем сообщение с клавиатурой
                    await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=inline_keyboard)
                else:
                    # Если нет ключей, добавляем кнопку "Создать ключ"
                    response_message = "У вас нет ключей."
                    
                    # Кнопка "Создать ключ"
                    create_key_button = types.InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[create_key_button]])
                    
                    await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard)

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при получении ключей: {e}")

    await callback_query.answer()

# Обработка запроса на просмотр информации о ключе
@router.callback_query(lambda c: c.data.startswith('view_key_'))
async def process_callback_view_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    key_name = callback_query.data.split('_', 2)[2]  # Получаем имя ключа

    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute('''
                SELECT k.key, c.expiry_time 
                FROM keys k
                JOIN connections c ON k.client_id = c.client_id
                WHERE c.tg_id = ? AND c.email = ?
            ''', (tg_id, key_name)) as cursor:
                record = await cursor.fetchone()

                if record:
                    key, expiry_time = record
                    expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
                    current_date = datetime.utcnow()
                    days_left = (expiry_date - current_date).days

                    days_left_message = f"Осталось дней: {days_left}" if days_left > 0 else "Ключ истек."
                    response_message = (f"Ваш ключ:\n<pre>{key}</pre>\n"
                                        f"Дата окончания: <b>{expiry_date.strftime('%Y-%m-%d %H:%M:%S')}</b>\n"
                                        f"{days_left_message}")

                    # Кнопки для продления и инструкций
                    renew_button = types.InlineKeyboardButton(text='Продлить ключ', callback_data='renew_key')
                    instructions_button = types.InlineKeyboardButton(text='Инструкции по использованию', callback_data='instructions')
                    back_button = types.InlineKeyboardButton(text='Назад в профиль', callback_data='view_profile')
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[renew_button], [instructions_button], [back_button]])

                    await bot.edit_message_text(response_message, chat_id=tg_id, message_id=callback_query.message.message_id, reply_markup=keyboard, parse_mode="HTML")
                else:
                    await bot.edit_message_text("Информация о ключе не найдена.", chat_id=tg_id, message_id=callback_query.message.message_id, parse_mode="HTML")

    except Exception as e:
        await handle_error(tg_id, callback_query, f"Ошибка при получении информации о ключе: {e}")

    await callback_query.answer()

# Остальные функции остаются без изменений...

# Обработка ошибок
async def handle_error(tg_id, callback_query, message):
    await bot.edit_message_text(message, chat_id=tg_id, message_id=callback_query.message.message_id, parse_mode="HTML")


# Обработка запроса на продление ключа
@router.callback_query(lambda c: c.data == 'renew_key')
async def process_callback_renew_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute('SELECT client_id, email, expiry_time FROM connections WHERE tg_id = ?', (tg_id,)) as cursor:
                record = await cursor.fetchone()

                if record:
                    client_id, email, expiry_time = record
                    current_time = datetime.utcnow().timestamp() * 1000
                    
                    if expiry_time <= current_time:
                        await callback_query.message.answer("Ваш ключ уже истек и не может быть продлен.")
                        return
                    
                    # Создаем клавиатуру для выбора плана продления
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                        [types.InlineKeyboardButton(text='Продлить на 1 месяц (100 руб.)', callback_data='renew_1_month')],
                        [types.InlineKeyboardButton(text='Продлить на 3 месяца (250 руб.)', callback_data='renew_3_months')],
                        [types.InlineKeyboardButton(text='Назад', callback_data='view_profile')]
                    ])

                    balance = await get_balance(tg_id)
                    response_message = (f"Выберите план продления:\n"
                                        f"Баланс: <b>{balance} руб.</b>\n"
                                        f"Действующий ключ истекает <b>{datetime.utcfromtimestamp(expiry_time / 1000).strftime('%Y-%m-%d %H:%M:%S')}</b>")

                    await delete_previous_message(callback_query)
                    await bot.send_message(tg_id, response_message, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        await callback_query.message.answer(f"Ошибка при выборе плана: {e}")

    await callback_query.answer()

# Обработка выбора плана продления
@router.callback_query(lambda c: c.data.startswith('renew_'))
async def process_callback_renew_plan(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    plan = callback_query.data.split('_')[1]  # '1' или '3'
    days_to_extend = 30 * int(plan)  # 30 дней или 90 дней
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute('SELECT client_id, email, expiry_time FROM connections WHERE tg_id = ?', (tg_id,)) as cursor:
                record = await cursor.fetchone()
                
                if record:
                    client_id = record[0]
                    email = record[1]
                    expiry_time = record[2]
                    current_time = datetime.utcnow().timestamp() * 1000
                    
                    if expiry_time <= current_time:
                        await callback_query.message.answer("Ваш ключ уже истек и не может быть продлен.")
                        return
                    
                    # Рассчитываем новый срок окончания, добавляя дни в зависимости от выбранного плана
                    new_expiry_time = int(expiry_time + timedelta(days=days_to_extend).total_seconds() * 1000)
                    
                    # Определяем стоимость продления
                    cost = 100 if plan == '1' else 250
                    
                    # Проверка баланса
                    balance = await get_balance(tg_id)
                    if balance < cost:
                        replenish_button = types.InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')
                        back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_keys')
                        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[replenish_button], [back_button]])
                        
                        await callback_query.message.answer("Недостаточно средств для продления ключа.", reply_markup=keyboard)
                        return
                    
                    # Создаем сессию для API-запросов
                    session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)
                    
                    # Обновляем ключ через API
                    success = extend_client_key(session, tg_id, client_id, email, new_expiry_time)
                    
                    if success:
                        await update_balance(tg_id, -cost)  # Списание средств с баланса
                        await db.execute('UPDATE connections SET expiry_time = ? WHERE client_id = ?', (new_expiry_time, client_id))
                        await db.commit()
                        response_message = f"Ваш ключ был успешно продлен на {days_to_extend // 30} месяц(-)."
                        back_button = types.InlineKeyboardButton(text='Назад', callback_data='view_keys')
                        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[back_button]])
                        await bot.send_message(tg_id, response_message, reply_markup=keyboard)
                    else:
                        await bot.send_message(tg_id, "Ошибка при продлении ключа.")
                else:
                    await bot.send_message(tg_id, "У вас нет ключей для продления.")
    
    except Exception as e:
        await bot.send_message(tg_id, f"Ошибка при продлении ключа: {e}")
    
    await callback_query.answer()


# Удаление предыдущего сообщения
async def delete_previous_message(callback_query: types.CallbackQuery):
    if callback_query.message.message_id:
        await bot.delete_message(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id)

# Обработка ошибок
async def handle_error(tg_id, callback_query, message):
    await delete_previous_message(callback_query)
    await bot.send_message(tg_id, message)
