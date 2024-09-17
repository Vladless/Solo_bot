from aiogram import types, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
import aiosqlite
from config import DATABASE_PATH
from database import get_balance, has_active_key

router = Router()

class ReplenishBalanceState(StatesGroup):
    choosing_transfer_method = State()
    waiting_for_admin_confirmation = State()

async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    username = callback_query.from_user.username

    try:
        # Проверяем наличие активного ключа
        email = await has_active_key(tg_id)
        expiry_date = "Нет активного ключа"
        
        if email:
            async with aiosqlite.connect(DATABASE_PATH) as db:
                async with db.execute(
                    "SELECT expiry_time FROM connections WHERE tg_id = ? AND expiry_time > ?",
                    (tg_id, int(datetime.utcnow().timestamp() * 1000))
                ) as cursor:
                    record = await cursor.fetchone()
                    if record:
                        expiry_date = datetime.utcfromtimestamp(record[0] / 1000).strftime("%Y-%m-%d %H:%M:%S")
        
        balance = await get_balance(tg_id)
        profile_message = (
            f"Профиль \n\n"
            f"ID: @{tg_id}\n"
            f"Ключ: {email if email else 'Нет активного ключа'}\n"
            f"Баланс: {balance} RUB\n"
        )
        
        # Кнопки для действий в профиле
        button_view_keys = InlineKeyboardButton(text='Мои ключи', callback_data='view_keys')
        button_replenish_balance = InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_view_keys], [button_replenish_balance]])

        profile_message += "\nВы можете просмотреть ваши ключи или пополнить баланс ниже:"
    
    except Exception as e:
        profile_message = f"Ошибка при получении данных профиля: {e}"
        keyboard = None
    
    # Отправляем сообщение с клавиатурой
    await callback_query.message.reply(profile_message, reply_markup=keyboard)
    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'view_profile')
async def view_profile_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)
