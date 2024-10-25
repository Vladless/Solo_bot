from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot import bot
from database import get_balance, get_key_count, get_referral_stats
from handlers.texts import profile_message_send, invite_message_send


class ReplenishBalanceState(StatesGroup):
    choosing_transfer_method = State()
    waiting_for_admin_confirmation = State()

router = Router()
async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.from_user.id
    username = callback_query.from_user.full_name  

    try:
        key_count = await get_key_count(tg_id)
        balance = await get_balance(tg_id)
        if balance is None:
            balance = 0 

        profile_message = (
            profile_message_send(username, tg_id, balance, key_count)
        )

        profile_message += (
            f"<b>Обязательно подпишитесь на канал</b> <a href='https://t.me/solonet_vpn'>здесь</a>\n"
        )
        
        if key_count == 0:
            profile_message += "\n<i>Нажмите ➕Устройство снизу чтобы добавить устройство в VPN</i>"
        
        button_create_key = InlineKeyboardButton(text='➕ Устройство', callback_data='create_key')
        button_view_keys = InlineKeyboardButton(text='📱 Мои устройства', callback_data='view_keys')
        button_replenish_balance = InlineKeyboardButton(text='💳 Пополнить баланс', callback_data='replenish_balance')
        button_invite = InlineKeyboardButton(text='👥 Пригласить', callback_data='invite')
        button_instructions = InlineKeyboardButton(text='📘 Инструкции', callback_data='instructions')
        button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_menu')
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [button_create_key, button_view_keys],  
            [button_replenish_balance],           
            [button_invite, button_instructions],   
            [button_back]                           
        ])

    except Exception as e:
        profile_message = f"❗️ Ошибка при получении данных профиля: {e}"
        keyboard = None
    
    await callback_query.message.delete()
    
    await bot.send_message(
        chat_id=tg_id, 
        text=profile_message,
        parse_mode='HTML',
        reply_markup=keyboard
    )
    
    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'invite')
async def invite_handler(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    referral_link = f"https://t.me/SoloNetVPN_bot?start=referral_{tg_id}"
    
    referral_stats = await get_referral_stats(tg_id)
    
    invite_message = (
        invite_message_send(referral_link,referral_stats)
    )
    
    button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='view_profile')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[button_back]])

    await callback_query.message.delete()

    await bot.send_message(
        chat_id=tg_id,
        text=invite_message,
        parse_mode='HTML',
        reply_markup=keyboard
    )

    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'view_profile')
async def view_profile_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await process_callback_view_profile(callback_query, state)