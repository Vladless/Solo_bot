from aiogram import F, Router
from aiogram.types import InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPPORT_CHAT_URL
from handlers.buttons import MAIN_MENU, SUPPORT
from handlers.texts import FALLBACK_MESSAGE

fallback_router = Router()


@fallback_router.message(F.text)
async def handle_unhandled_messages(message: Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    keyboard.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await message.answer(
        FALLBACK_MESSAGE,
        reply_markup=keyboard.as_markup(),
    )
