from aiogram import types, Router
import re
from database import get_active_key_email
from auth import link, login_with_credentials
from datetime import datetime
import aiosqlite
from config import DATABASE_PATH 
from config import ADMIN_USERNAME, ADMIN_PASSWORD


router = Router()
session = login_with_credentials(ADMIN_USERNAME, ADMIN_PASSWORD)

@router.callback_query(lambda c: c.data == 'view_stats')
async def process_callback_view_stats(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
    try:
        email = await get_active_key_email(tg_id)
        if email:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute("SELECT client_id FROM connections WHERE tg_id = ? AND expiry_time > ?", 
                                      (tg_id, int(datetime.utcnow().timestamp() * 1000))) as cursor:
                    record = await cursor.fetchone()
                    if record:
                        client_id = record[0]
                        connection_link = link(session, client_id, email)
                        
                        up_match = re.search(r'up=(\d+)', connection_link)
                        down_match = re.search(r'down=(\d+)', connection_link)
                        
                        up = up_match.group(1) if up_match else "Неизвестно"
                        down = down_match.group(1) if down_match else "Неизвестно"
                        
                        statistics = f"Статистика вашего ключа:\nЗагрузка: {up} MB\nВыгрузка: {down} MB"
                    else:
                        statistics = "У вас нет активных ключей."
        else:
            statistics = "У вас нет активных ключей."
    
    except Exception as e:
        statistics = f"Ошибка при получении статистики: {e}"
    
    await callback_query.message.reply(f"Ваша статистика:\n{statistics}")
    await callback_query.answer()
