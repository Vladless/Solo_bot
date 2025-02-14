import os
from typing import Any

import aiofiles
from aiogram import F, Router, types
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import CONNECT_MACOS, CONNECT_WINDOWS, SUPPORT_CHAT_URL

from database import get_key_details
from handlers.texts import (
    CONNECT_TV_TEXT,
    INSTRUCTION_PC,
    INSTRUCTIONS,
    KEY_MESSAGE,
    SUBSCRIPTION_DETAILS_TEXT,
)
from logger import logger

router = Router()


@router.callback_query(F.data == "instructions")
@router.message(F.text == "/instructions")
async def send_instructions(
    callback_query_or_message: CallbackQuery | Message,
):
    instructions_message = INSTRUCTIONS
    image_path = os.path.join("img", "instructions.jpg")

    if not os.path.isfile(image_path):
        if isinstance(callback_query_or_message, CallbackQuery):
            await callback_query_or_message.message.answer("Файл изображения не найден.")
        else:
            await callback_query_or_message.answer("Файл изображения не найден.")
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_CHAT_URL))
    builder.row(
        InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"),
    )

    if isinstance(callback_query_or_message, CallbackQuery):
        send_photo = callback_query_or_message.message.answer_photo
    else:
        send_photo = callback_query_or_message.answer_photo

    async with aiofiles.open(image_path, "rb") as image_from_buffer:
        image_data = await image_from_buffer.read()
        await send_photo(
            BufferedInputFile(image_data, filename="instructions.jpg"),
            caption=instructions_message,
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data.startswith("connect_pc|"))
async def process_connect_pc(callback_query: CallbackQuery, session: Any):
    key_name = callback_query.data.split("|")[1]

    record = await get_key_details(key_name, session)

    if not record:
        await callback_query.message.answer("❌ <b>Ключ не найден. Проверьте имя ключа.</b> 🔍")
        return

    key = record["key"]
    key_message = KEY_MESSAGE.format(key)
    instruction_message = f"{key_message}{INSTRUCTION_PC}"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💻 Подключить Windows", url=f"{CONNECT_WINDOWS}{key}"))
    builder.row(InlineKeyboardButton(text="💻 Подключить MacOS", url=f"{CONNECT_MACOS}{key}"))
    builder.row(InlineKeyboardButton(text="🆘 Поддержка", url=f"{SUPPORT_CHAT_URL}"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await callback_query.message.answer(
        instruction_message,
        reply_markup=builder.as_markup(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("connect_tv|"))
async def process_connect_tv(callback_query: CallbackQuery):
    key_name = callback_query.data.split("|")[1]

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="▶ Продолжить", callback_data=f"continue_tv|{key_name}"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await callback_query.message.answer(
        text=CONNECT_TV_TEXT,
        reply_markup=builder.as_markup(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("continue_tv|"))
async def process_continue_tv(callback_query: CallbackQuery, session: Any):
    key_name = callback_query.data.split("|")[1]
    tg_id = callback_query.from_user.id

    logger.info(f"tg_id: {tg_id}, key_name: {key_name}")

    record = await get_key_details(key_name, session)

    logger.info(f"Query result: {record}")

    subscription_link = record["key"]

    message_text = SUBSCRIPTION_DETAILS_TEXT.format(subscription_link=subscription_link)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📖 Полная инструкция", url="https://vpn4tv.com/quick-guide.html"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await callback_query.message.answer(text=message_text, reply_markup=builder.as_markup())
