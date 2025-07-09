from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPPORT_CHAT_URL
from handlers.localization import get_user_texts, get_user_buttons

fallback_router = Router()


@fallback_router.message(F.text)
async def handle_unhandled_messages(message: Message, session):
    # Получаем локализованные тексты и кнопки для пользователя
    texts = await get_user_texts(session, message.chat.id)
    buttons = await get_user_buttons(session, message.chat.id)
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text=buttons.SUPPORT, url=SUPPORT_CHAT_URL))
    keyboard.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    await message.answer(
        texts.FALLBACK_MESSAGE,
        reply_markup=keyboard.as_markup(),
    )
