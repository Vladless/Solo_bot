from aiogram import types, Router
import aiosqlite
from config import DATABASE_PATH 
from bot import bot
from datetime import datetime

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
                    
                    # Добавляем кнопку с инструкциями
                    instructions_button = types.InlineKeyboardButton(text='Инструкции по использованию', callback_data='instructions')
                    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[instructions_button]])
                    
                    await bot.send_message(tg_id, response_message, parse_mode="HTML", reply_markup=keyboard)
                else:
                    response_message = "У вас нет ключей."
                    await bot.send_message(tg_id, response_message, reply_to_message_id=callback_query.message.message_id)
    
    except Exception as e:
        response_message = f"Ошибка при получении ключей: {e}"
        await bot.send_message(tg_id, response_message, reply_to_message_id=callback_query.message.message_id)
    
    await callback_query.answer()
