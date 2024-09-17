from aiogram import types, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

router = Router()

@router.message(Command("start"))
async def start_command(message: types.Message):
    welcome_text = "Добро пожаловать! Вы можете создать ключ для подключения VPN, просмотреть статистику использования, узнать дату окончания ключа или просмотреть ваш профиль."

    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
    button_view_stats = InlineKeyboardButton(text='Посмотреть статистику', callback_data='view_stats')
    button_view_expiry = InlineKeyboardButton(text='Дата окончания ключа', callback_data='view_expiry')
    button_view_profile = InlineKeyboardButton(text='Мой профиль', callback_data='view_profile')
    
    # Основное меню
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [button_create_key],
        [button_view_stats],
        [button_view_expiry],
        [button_view_profile]
    ])
    
    # Отправка приветственного сообщения
    await message.reply(welcome_text, reply_markup=keyboard)

    # Кнопка "В начало" для возвращения
    main_menu_keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="В начало")]], 
        resize_keyboard=True
    )
    
    await message.answer("Чтобы вернуться в начало, нажмите кнопку ниже:", reply_markup=main_menu_keyboard)
