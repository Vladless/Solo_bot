from aiogram import types, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from config import ADMIN_CHAT_ID

router = Router()

@router.message(Command("start"))
async def start_command(message: types.Message):
    welcome_text = "Добро пожаловать! Вы можете создать ключ для подключения VPN, просмотреть статистику использования, узнать дату окончания ключа или просмотреть ваш профиль."

    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
    button_view_stats = InlineKeyboardButton(text='Посмотреть статистику', callback_data='view_stats')
    button_view_expiry = InlineKeyboardButton(text='Дата окончания ключа', callback_data='view_expiry')
    button_view_profile = InlineKeyboardButton(text='Мой профиль', callback_data='view_profile')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [button_create_key],
        [button_view_stats],
        [button_view_expiry],
        [button_view_profile]
    ])
    
    main_menu_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="В начало")]], resize_keyboard=True)
    
    await message.reply(welcome_text, reply_markup=keyboard)
    await message.answer("Чтобы вернуться в начало, нажмите кнопку ниже:", reply_markup=main_menu_keyboard)
