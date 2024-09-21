from aiogram import types, Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
from database import get_balance, get_key_count

router = Router()

class ReplenishBalanceState(StatesGroup):
    choosing_transfer_method = State()
    waiting_for_admin_confirmation = State()

async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id

    try:
        # Получаем количество ключей
        key_count = await get_key_count(tg_id)
        
        # Получаем баланс
        balance = await get_balance(tg_id)
        if balance is None:
            balance = 0  # По умолчанию 0, если баланс неизвестен
        
        profile_message = (
            f"<b>Профиль пользователя</b>\n\n"
            f"🔹 <b>ID:</b> {tg_id}\n"
            f"🔹 <b>Количество ключей:</b> {key_count}\n"
            f"🔹 <b>Баланс:</b> {balance} RUB\n"
            f"🔹 <b>Дата регистрации:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        
        # Кнопки для действий в профиле
        button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
        button_view_keys = InlineKeyboardButton(text='Мои ключи', callback_data='view_keys')
        button_replenish_balance = InlineKeyboardButton(text='Пополнить баланс', callback_data='replenish_balance')
        button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_menu')  # Добавляем кнопку "Назад"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [button_create_key],  # Добавляем кнопку "Создать ключ"
            [button_view_keys],
            [button_replenish_balance],
            [button_back]  # Добавляем кнопку "Назад" в клавиатуру
        ])

    except Exception as e:
        profile_message = f"❗️ Ошибка при получении данных профиля: {e}"
        keyboard = None
    
    # Редактируем существующее сообщение, а не отправляем новое
    await callback_query.message.edit_text(
        text=profile_message,
        parse_mode='HTML',
        reply_markup=keyboard
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'view_profile')
async def view_profile_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)
