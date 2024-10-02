import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message)

from bot import bot
from config import (ADMIN_ID, CHANNEL_URL, 
                    SUPPORT_CHAT_URL)

router = Router()

class FeedbackState(StatesGroup):
    waiting_for_feedback = State()

async def send_welcome_message(chat_id: int):
    # Новый текст приветствия
    welcome_text = (
        "*SoloNet — ваш доступ в свободный интернет! 🌐✨*\n\n"
        "Используйте надежный и быстрый VPN, который гарантирует вашу безопасность даже в самых строгих условиях. 🔒🚀\n\n"
        "*ver. 1.0*"
    )

    # Путь к изображению
    image_path = os.path.join(os.path.dirname(__file__), 'pic.jpg')

    # Проверка существования файла
    if not os.path.isfile(image_path):
        await bot.send_message(chat_id, "Файл изображения не найден.")
        return

    # Создаем inline-клавиатуру
    inline_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='👤 Мой профиль', callback_data='view_profile')],
        [InlineKeyboardButton(text='🔒 О VPN', callback_data='about_vpn')],
        [InlineKeyboardButton(text='📞 Поддержка', url=SUPPORT_CHAT_URL)],  # Изменили на кнопку Поддержка
        [InlineKeyboardButton(text='📢 Наш канал', url=CHANNEL_URL)]
    ])

    # Отправляем изображение с инлайн-клавиатурой
    with open(image_path, 'rb') as image_from_buffer:
        await bot.send_photo(
            chat_id,
            BufferedInputFile(image_from_buffer.read(), filename="pic.jpg"),
            caption=welcome_text,
            parse_mode='Markdown',
            reply_markup=inline_keyboard  # Inline-клавиатура
        )

@router.message(Command('start'))
async def start_command(message: Message):
    await send_welcome_message(message.chat.id)

@router.callback_query(lambda c: c.data == 'about_vpn')
async def handle_about_vpn(callback_query: CallbackQuery):
    # Удаляем сообщение главного меню
    await callback_query.message.delete()

    info_message = (
        "*О VPN*\n\n"
        "Мы используем высокоскоростные серверы в разных локациях и выдаём ключ каждому индивидуально. "
        "Также мы применяем новейшие протоколы шифрования для обеспечения безопасности ваших данных.\n\n"
        "<b>Ваш ключ — ваша безопасность! Не передавайте своё шифрование сторонним лицам.</b>"
    )

    
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
    # Удаляем текущее сообщение
    await callback_query.message.delete()
    
    # Отправляем приветственное сообщение
    await send_welcome_message(callback_query.from_user.id)
    await callback_query.answer()
