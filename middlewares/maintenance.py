from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from config import ADMIN_ID

maintenance_mode = False

class MaintenanceModeMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if maintenance_mode:
            user_id = None
            if isinstance(event, Message):
                user_id = event.from_user.id
            elif isinstance(event, CallbackQuery):
                user_id = event.from_user.id

            if user_id and user_id not in ADMIN_ID:
                await event.answer("⚙️ Бот временно недоступен. Ведутся технические работы.")
                return
        
        return await handler(event, data)
