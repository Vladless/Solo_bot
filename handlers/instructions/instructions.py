import os
from typing import Any

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CONNECT_MACOS, CONNECT_WINDOWS, SUPPORT_CHAT_URL
from handlers.texts import INSTRUCTION_PC, INSTRUCTIONS, KEY_MESSAGE

router = Router()


@router.callback_query(F.data == "instructions")
async def send_instructions(callback_query: types.CallbackQuery):
    instructions_message = INSTRUCTIONS
    image_path = os.path.join("img", "instructions.jpg")
    if not os.path.isfile(image_path):
        await callback_query.message.answer("Файл изображения не найден.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_CHAT_URL))
    builder.row(
        InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"),
    )

    with open(image_path, "rb") as image_from_buffer:
        await callback_query.message.answer_photo(
            BufferedInputFile(image_from_buffer.read(), filename="instructions.jpg"),
            caption=instructions_message,
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data.startswith("connect_pc|"))
async def process_connect_pc(callback_query: types.CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]

    record = await session.fetchrow(
        """
        SELECT k.key
        FROM keys k
        WHERE k.tg_id = $1 AND k.email = $2
        """,
        tg_id,
        key_name,
    )

    if not record:
        await callback_query.message.answer(
            "❌ <b>Ключ не найден. Проверьте имя ключа.</b> 🔍"
        )
        return

    key = record["key"]
    key_message = KEY_MESSAGE.format(key)
    instruction_message = f"{key_message}{INSTRUCTION_PC}"

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💻 Подключить Windows", url=f"{CONNECT_WINDOWS}{key}"
        )
    )
    builder.row(
        InlineKeyboardButton(text="💻 Подключить MacOS", url=f"{CONNECT_MACOS}{key}")
    )
    builder.row(InlineKeyboardButton(text="🆘 Поддержка", url=f"{SUPPORT_CHAT_URL}"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await callback_query.message.answer(
        instruction_message,
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
