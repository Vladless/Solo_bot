from aiogram import Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from aiogram.types import BufferedInputFile
import os

from bot import bot
from config import ADMIN_ID, CHANNEL_URL

router = Router()

class FeedbackState(StatesGroup):
    waiting_for_feedback = State()

async def send_welcome_message(chat_id: int):
    # Новый текст приветствия
    welcome_text = (
        "*SoloNet — ваш провайдер в бесцензурный интернет!*\n\n"
        "Получите высокую скорость и самый безопасный протокол VPN, "
        "который работает даже в Китае."
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
        [InlineKeyboardButton(text='📝 Обратная связь', callback_data='feedback')],
        [InlineKeyboardButton(text='📢 Наш канал', url=CHANNEL_URL)]
    ])

    # Отправляем изображение с инлайн-клавиатурой
    with open(image_path, 'rb') as image_from_buffer:
        await bot.send_photo(
            chat_id,
            BufferedInputFile(image_from_buffer.read(), filename="solo_pic.png"),
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
        "Мы используем VLESS для обеспечения безопасного и надежного подключения к интернету. "
        "Каждому пользователю предоставляется индивидуальный ключ для подключения. "
        "Этот ключ необходим для использования нашего VPN-сервиса."
    )
    
    button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_menu')
    inline_keyboard_back = InlineKeyboardMarkup(inline_keyboard=[[button_back]])

    await callback_query.message.answer(
        info_message,
        parse_mode='Markdown',
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

@router.callback_query(lambda c: c.data == 'feedback')
async def handle_feedback(callback_query: CallbackQuery, state: FSMContext):
    # Удаляем сообщение главного меню
    await callback_query.message.delete()

    feedback_text = "Напишите нам, если у вас возникли трудности с подключением, есть отзыв или предложение. @pocomacho"

    button_back = InlineKeyboardButton(text='⬅️ Назад', callback_data='back_to_menu')
    inline_keyboard_back = InlineKeyboardMarkup(inline_keyboard=[[button_back]])

    await callback_query.message.answer(
        feedback_text,
        parse_mode='Markdown',
        reply_markup=inline_keyboard_back
    )

    await state.set_state(FeedbackState.waiting_for_feedback)
    await state.update_data(user_id=callback_query.from_user.id)
    await callback_query.answer()

@router.message(FeedbackState.waiting_for_feedback)
async def receive_feedback(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    user_id = user_data.get('user_id')

    feedback_message = (
        f"Обратная связь от пользователя {user_id}:\n\n"
        f"{message.text}"
    )

    try:
        await bot.send_message(ADMIN_ID, feedback_message)
        await message.answer("Спасибо за ваше сообщение! Мы свяжемся с вами, если это будет необходимо.")
    except Exception as e:
        await message.answer("Произошла ошибка при отправке вашего сообщения.")
        print(f"Ошибка при отправке обратной связи: {e}")  # Логирование ошибок

    await state.clear()
