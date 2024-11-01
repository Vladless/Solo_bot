import os
from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)
from handlers.texts import ABOUT_VPN, WELCOME_TEXT
from bot import bot
from config import CHANNEL_URL, SUPPORT_CHAT_URL, APP_URL
from database import add_connection, add_referral, check_connection_exists, get_trial
from handlers.keys.trial_key import create_trial_key  
from handlers.texts import INSTRUCTIONS_TRIAL

router = Router()

class FeedbackState(StatesGroup):
    waiting_for_feedback = State()

async def send_welcome_message(chat_id: int, trial_status: int):
    welcome_text = WELCOME_TEXT

    image_path = os.path.join(os.path.dirname(__file__), 'pic.jpg')

    if not os.path.isfile(image_path):
        await bot.send_message(chat_id, "Файл изображения не найден.")
        return

    inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔗 Подключить VPN', callback_data='connect_vpn')] if trial_status == 0 else [],
        [InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')],
        [InlineKeyboardButton(text='🔒 О VPN', callback_data='about_vpn')],
        [InlineKeyboardButton(text='📞 Поддержка', url=SUPPORT_CHAT_URL)],
        [InlineKeyboardButton(text='📢 Наш канал', url=CHANNEL_URL)],
    ])

    inline_keyboard.inline_keyboard = [row for row in inline_keyboard.inline_keyboard if row]

    with open(image_path, 'rb') as image_from_buffer:
        await bot.send_photo(
            chat_id,
            BufferedInputFile(image_from_buffer.read(), filename="pic.jpg"),
            caption=welcome_text,
            parse_mode='HTML',
            reply_markup=inline_keyboard 
        )

@router.message(Command('start'))
async def start_command(message: Message):
    print(f"Received start command with text: {message.text}") 
    if 'referral_' in message.text:
        referrer_tg_id = int(message.text.split('referral_')[1])
        print(f"Referral ID: {referrer_tg_id}")
        if not await check_connection_exists(message.from_user.id):
            await add_connection(message.from_user.id)
            await add_referral(message.from_user.id, referrer_tg_id)
            await message.answer("Вас пригласил друг, добро пожаловать!")
        else:
            await message.answer("Вы уже зарегистрированы в системе!")
    
    trial_status = await get_trial(message.from_user.id)
    await send_welcome_message(message.chat.id, trial_status)

@router.callback_query(lambda c: c.data == 'connect_vpn')
async def handle_connect_vpn(callback_query: CallbackQuery):
    await callback_query.message.delete()
    user_id = callback_query.from_user.id

    trial_key_info = await create_trial_key(user_id)

    if 'error' in trial_key_info:
        await callback_query.message.answer(trial_key_info['error'])
    else:
        key_message = (
            f"<b>Ваш ключ доступа:</b>\n<pre>{trial_key_info['key']}</pre>\n\n"
            f"<b>Инструкции:</b>\n{INSTRUCTIONS_TRIAL}"
        )

        button_profile = InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')
        
        button_iphone = InlineKeyboardButton(
            text='🍏IPhone', 
            url=f'{APP_URL}/?url=v2raytun://import/{trial_key_info["key"]}'
        )
        button_android = InlineKeyboardButton(
            text='🤖Android', 
            url=f'{APP_URL}/?url=v2raytun://import-sub?url={trial_key_info["key"]}'
        )

        inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [button_iphone, button_android],
            [button_profile]
        ])

        await callback_query.message.answer(
            key_message,
            parse_mode='HTML',
            reply_markup=inline_keyboard
        )

    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'about_vpn')
async def handle_about_vpn(callback_query: CallbackQuery):
    await callback_query.message.delete()
    info_message = ABOUT_VPN

    button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_menu')
    inline_keyboard_back = InlineKeyboardMarkup(inline_keyboard=[[button_back]])

    await callback_query.message.answer(
        info_message,
        parse_mode='HTML',
        reply_markup=inline_keyboard_back
    )
    await callback_query.answer()

@router.callback_query(lambda c: c.data == 'back_to_menu')
async def handle_back_to_menu(callback_query: CallbackQuery):
    await callback_query.message.delete()
    trial_status = await get_trial(callback_query.from_user.id)
    await send_welcome_message(callback_query.from_user.id, trial_status)
    await callback_query.answer()
