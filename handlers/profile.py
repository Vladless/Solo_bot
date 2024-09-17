from aiogram import types, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database import get_active_key_email, get_balance
from datetime import datetime
import aiosqlite
from aiogram.fsm.context import FSMContext
from config import DATABASE_PATH 

router = Router()

@router.callback_query(lambda c: c.data == 'view_profile')
async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    username = callback_query.from_user.username

    try:
        email = await get_active_key_email(tg_id)
        if email:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute("SELECT expiry_time FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                                      (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
                    record = await cursor.fetchone()
                    if record:
                        expiry_time = record[0]
                        expiry_date = datetime.utcfromtimestamp(expiry_time / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        expiry_date = "Неизвестно"
            
            balance = await get_balance(tg_id)
            profile_message = (
                f"Профиль клиента:\n"
                f"Никнейм: @{username}\n"
                f"Email: {email}\n"
                f"Дата окончания ключа: {expiry_date}\n"
                f"Баланс: {balance}\n"
            )
            
            button_view_keys = InlineKeyboardButton(text='Мои ключи', callback_data='view_keys')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_view_keys]])
            
            profile_message += "Вы можете просмотреть ваши ключи ниже:"
        else:
            profile_message = "У вас нет активных ключей."
            keyboard = None
    
    except Exception as e:
        profile_message = f"Ошибка при получении данных профиля: {e}"
        keyboard = None
    
    await callback_query.message.reply(profile_message, reply_markup=keyboard)
    await callback_query.answer()
