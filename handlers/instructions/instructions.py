import os

from aiogram import types
from aiogram.types import (BufferedInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup)

from handlers.texts import INSTRUCTIONS

async def send_instructions(callback_query: types.CallbackQuery):
    await callback_query.message.delete()

    instructions_message = (
        INSTRUCTIONS
    )

    image_path = os.path.join(os.path.dirname(__file__), 'instructions.jpg')

    if not os.path.isfile(image_path):
        await callback_query.message.answer("Файл изображения не найден.")
        await callback_query.answer()
        return

    back_button = InlineKeyboardButton(text='🔙 Назад', callback_data='back_to_main')
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

    with open(image_path, 'rb') as image_from_buffer:
        await callback_query.message.answer_photo(
            BufferedInputFile(image_from_buffer.read(), filename="instructions.jpg"),
            caption=instructions_message,
            parse_mode='Markdown',
            reply_markup=keyboard
        )
    
    await callback_query.answer()
