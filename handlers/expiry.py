from aiogram import types, Router
from database import get_active_key_email
from datetime import datetime
import aiosqlite
from config import DATABASE_PATH 

router = Router()

@router.callback_query(lambda c: c.data == 'view_expiry')
async def process_callback_view_expiry(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
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
                        message_text = f"Дата окончания вашего ключа: {expiry_date}"
                    else:
                        message_text = "У вас нет активных ключей."
        else:
            message_text = "У вас нет активных ключей."
    
    except Exception as e:
        message_text = f"Ошибка при получении даты окончания ключа: {e}"
    
    await callback_query.message.reply(message_text)
    await callback_query.answer()
