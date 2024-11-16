import os

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from bot import bot
from config import CONNECT_WINDOWS, SUPPORT_CHAT_URL
from handlers.texts import INSTRUCTION_PC, INSTRUCTIONS, KEY_MESSAGE
from logger import logger

router = Router()


async def send_instructions(callback_query: types.CallbackQuery):
    await callback_query.message.delete()

    instructions_message = INSTRUCTIONS

    image_path = os.path.join(os.path.dirname(__file__), "instructions.jpg")

    if not os.path.isfile(image_path):
        await callback_query.message.answer("Файл изображения не найден.")
        await callback_query.answer()
        return

    back_button = InlineKeyboardButton(
        text="⬅️ Вернуться в профиль", callback_data="view_profile"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[back_button]])

    with open(image_path, "rb") as image_from_buffer:
        await callback_query.message.answer_photo(
            BufferedInputFile(image_from_buffer.read(), filename="instructions.jpg"),
            caption=instructions_message,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    await callback_query.answer()


@router.callback_query(F.data.startswith("connect_pc|"))
async def process_connect_pc(callback_query: types.CallbackQuery):
    tg_id = callback_query.from_user.id
    key = callback_query.data.split("|")[1]

    try:
        await bot.delete_message(
            chat_id=tg_id, message_id=callback_query.message.message_id
        )
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")

    key_message = KEY_MESSAGE.format(key)

    instruction_message = f"{key_message}{INSTRUCTION_PC}"

    connect_windows_button = types.InlineKeyboardButton(
        text="💻 Подключить Windows", url=f"{CONNECT_WINDOWS}{key}"
    )

    support_button = types.InlineKeyboardButton(
        text="🆘 Поддержка", url=f"{SUPPORT_CHAT_URL}"
    )

    back_button = types.InlineKeyboardButton(
        text="🔙 Назад в профиль", callback_data="view_profile"
    )

    inline_keyboard = [[connect_windows_button], [support_button], [back_button]]
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

    await bot.send_message(
        tg_id, instruction_message, reply_markup=keyboard, parse_mode="HTML"
    )

    await callback_query.answer()
