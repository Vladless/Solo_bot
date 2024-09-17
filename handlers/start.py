from aiogram import types, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

router = Router()

async def start_command(message: types.Message, callback_data: str = None):
    welcome_text = (
        "Добро пожаловать! Вы можете создать ключ для подключения VPN, "
        "просмотреть статистику использования, узнать дату окончания ключа или "
        "просмотреть ваш профиль."
    )

    # Создаем кнопки для команд
    button_create_key = InlineKeyboardButton(text='Создать ключ', callback_data='create_key')
#    button_view_stats = InlineKeyboardButton(text='Посмотреть статистику', callback_data='view_stats')
#    button_view_expiry = InlineKeyboardButton(text='Дата окончания ключа', callback_data='view_expiry')
    button_view_profile = InlineKeyboardButton(text='Мой профиль', callback_data='view_profile')

    # Создаем клавиатуру с кнопками
    inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [button_create_key],
#        [button_view_stats],
#        [button_view_expiry],
        [button_view_profile]
    ])

    # Создаем кнопку для возврата в начало
    reply_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="В начало")],
            [KeyboardButton(text="Мой профиль")]
        ],
        resize_keyboard=True
    )

    # Отправляем сообщение с кнопками
    await message.reply(welcome_text, reply_markup=inline_keyboard)
    await message.answer(
        "",
        reply_markup=reply_keyboard
    )