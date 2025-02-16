import os
from typing import Any

from aiogram import F, Router, types
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
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
from handlers.utils import edit_or_send_message

router = Router()


@router.callback_query(F.data == "instructions")
@router.message(F.text == "/instructions")
async def send_instructions(callback_query_or_message: CallbackQuery | Message):
    instructions_message = INSTRUCTIONS
    image_path = os.path.join("img", "instructions.jpg")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    if isinstance(callback_query_or_message, CallbackQuery):
        target_message = callback_query_or_message.message
    else:
        target_message = callback_query_or_message

    await edit_or_send_message(
        target_message=target_message,
        text=instructions_message,
        reply_markup=builder.as_markup(),
        media_path=image_path,
    )


@router.callback_query(F.data.startswith("connect_pc|"))
async def process_connect_pc(callback_query: CallbackQuery, session: Any):
    key_name = callback_query.data.split("|")[1]
    record = await get_key_details(key_name, session)
    if not record:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ <b>Ключ не найден. Проверьте имя ключа.</b> 🔍",
            reply_markup=types.InlineKeyboardMarkup(),
            media_path=None,
        )
        return

    key = record["key"]
    key_message_text = KEY_MESSAGE.format(key)
    instruction_message = f"{key_message_text}{INSTRUCTION_PC}"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💻 Подключить Windows", url=f"{CONNECT_WINDOWS}{key}"))
    builder.row(InlineKeyboardButton(text="💻 Подключить MacOS", url=f"{CONNECT_MACOS}{key}"))
    builder.row(InlineKeyboardButton(text="🆘 Поддержка", url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_key|{key_name}"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=instruction_message,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_tv|"))
async def process_connect_tv(callback_query: CallbackQuery):
    key_name = callback_query.data.split("|")[1]

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="▶ Продолжить", callback_data=f"continue_tv|{key_name}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_key|{key_name}"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=CONNECT_TV_TEXT,
        reply_markup=builder.as_markup(),
        media_path=None,
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("continue_tv|"))
async def process_continue_tv(callback_query: CallbackQuery, session: Any):
    key_name = callback_query.data.split("|")[1]
    tg_id = callback_query.from_user.id

    record = await get_key_details(key_name, session)
    subscription_link = record["key"]
    message_text = SUBSCRIPTION_DETAILS_TEXT.format(subscription_link=subscription_link)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📖 Полная инструкция", url="https://vpn4tv.com/quick-guide.html"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_key|{key_name}"))
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message, text=message_text, reply_markup=builder.as_markup(), media_path=None
    )
