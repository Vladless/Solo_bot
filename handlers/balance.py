import aiosqlite
from aiogram import Router, types

router = Router()

@router.callback_query(lambda c: c.data == 'view_balance')
async def process_callback_view_balance(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    
   
