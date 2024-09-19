from aiogram import types, Router
import aiosqlite
from bot import bot
from datetime import datetime, timedelta
from database import get_active_key_email, get_balance, store_key, update_balance
from client import extend_client_key
from client import extend_client_key, login_with_credentials
from config import ADMIN_ID, ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_PATH

router = Router()

@router.callback_query(lambda c: c.data == 'view_keys')
async def process_callback_view_keys(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute('''
                SELECT k.key, c.expiry_time 
                FROM keys k
                JOIN connections c ON k.client_id = c.client_id
                WHERE c.tg_id = ?
            ''', (tg_id,)) as cursor:
                record = await cursor.fetchone()
                
                if record:
                    key = record[0]
                    expiry_time = record[1]
                    expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    response_message = f"Ваш ключ:\n<pre>{key}</pre>\nДата окончания: <b>{expiry_date}</b>"
                    
                    # Кнопки для инструкций и продления
                    instructions_button = types.InlineKeyboardButton(text='Инструкции по использованию', callback_data='instructions')
                    renew_button = types.InlineKeyboardButton(text='Продлить ключ', callback_data='renew_key')
                    
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
                        [instructions_button],
                        [renew_button]
                    ])
                    
                    await bot.send_message(tg_id, response_message, parse_mode="HTML", reply_markup=keyboard)
                else:
                    response_message = "У вас нет ключей."
                    await bot.send_message(tg_id, response_message, reply_to_message_id=callback_query.message.message_id)
    
    except Exception as e:
        response_message = f"Ошибка при получении ключей: {e}"
        await bot.send_message(tg_id, response_message, reply_to_message_id=callback_query.message.message_id)
    
    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'renew_key')
async def process_callback_renew_key(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute('SELECT client_id, email, expiry_time FROM connections WHERE tg_id = ?', (tg_id,)) as cursor:
                record = await cursor.fetchone()
                
                if record:
                    client_id = record[0]
                    email = record[1]  # Получаем email
                    expiry_time = record[2]
                    current_time = datetime.utcnow().timestamp() * 1000
                    new_expiry_time = int((datetime.utcnow() + timedelta(days=30)).timestamp() * 1000)
                    
                    if expiry_time <= current_time:
                        await callback_query.message.answer("Ваш ключ уже истек и не может быть продлен.")
                        return
                    
                    # Проверка баланса
                    balance = await get_balance(tg_id)
                    if balance < 100:
                        await callback_query.message.answer("Недостаточно средств для продления ключа.")
                        return
                    
                    # Создаем сессию для API-запросов
                    session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)
                    
                    # Обновляем ключ через API
                    success = extend_client_key(session, tg_id, client_id, email, new_expiry_time)
                    
                    if success:
                        await update_balance(tg_id, -100)  # Списание 100 рублей с баланса
                        await db.execute('UPDATE connections SET expiry_time = ? WHERE client_id = ?', (new_expiry_time, client_id))
                        await db.commit()
                        await callback_query.message.answer("Ваш ключ был успешно продлен на месяц.")
                    else:
                        await callback_query.message.answer("Ошибка при продлении ключа.")
                else:
                    await callback_query.message.answer("У вас нет ключей для продления.")
    
    except Exception as e:
        await callback_query.message.answer(f"Ошибка при продлении ключа: {e}")
    
    await callback_query.answer()