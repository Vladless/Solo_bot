from aiogram import types, Router
import aiosqlite

router = Router()

@router.callback_query(lambda c: c.data == 'view_balance')
async def process_callback_view_balance(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
   
