import os
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    CONNECT_MACOS,
    CONNECT_WINDOWS,
    DOWNLOAD_MACOS,
    DOWNLOAD_PC,
    SUPPORT_CHAT_URL,
)
from database import get_key_details
from handlers.localization import get_user_texts, get_user_buttons
from handlers.utils import edit_or_send_message

router = Router()


@router.callback_query(F.data == "instructions")
@router.message(F.text == "/instructions")
async def send_instructions(callback_query_or_message: CallbackQuery | Message, session: AsyncSession):
    if isinstance(callback_query_or_message, CallbackQuery):
        user_id = callback_query_or_message.from_user.id
        target_message = callback_query_or_message.message
    else:
        user_id = callback_query_or_message.from_user.id
        target_message = callback_query_or_message

    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    instructions_message = texts.INSTRUCTIONS
    image_path = os.path.join("img", "instructions.jpg")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=buttons.SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=target_message,
        text=instructions_message,
        reply_markup=builder.as_markup(),
        media_path=image_path,
    )


@router.callback_query(F.data.startswith("connect_pc|"))
async def process_connect_pc(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    key_name = callback_query.data.split("|")[1]
    record = await get_key_details(session, key_name)
    if not record:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=texts.KEY_NOT_FOUND_ERROR,
            reply_markup=builder.as_markup(),
            media_path=None,
        )
        return

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=buttons.PC_PC, callback_data=f"windows_menu|{key_name}")
    )
    builder.row(
        InlineKeyboardButton(text=buttons.PC_MACOS, callback_data=f"macos_menu|{key_name}")
    )
    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data=f"view_key|{key_name}"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=texts.CHOOSE_DEVICE_TEXT,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("windows_menu|"))
async def process_windows_menu(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    key_name = callback_query.data.split("|")[1]
    record = await get_key_details(session, key_name)
    key = record["key"]
    key_message_text = texts.KEY_MESSAGE.format(key)
    instruction_message = f"{key_message_text}{texts.INSTRUCTION_PC}"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=buttons.DOWNLOAD_PC_BUTTON, url=DOWNLOAD_PC))
    builder.row(
        InlineKeyboardButton(text=buttons.CONNECT_WINDOWS_BUTTON, url=f"{CONNECT_WINDOWS}{key}")
    )
    builder.row(InlineKeyboardButton(text=buttons.SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data=f"connect_pc|{key_name}"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=instruction_message,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("macos_menu|"))
async def process_macos_menu(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    key_name = callback_query.data.split("|")[1]
    record = await get_key_details(session, key_name)
    key = record["key"]
    key_message_text = texts.KEY_MESSAGE.format(key)
    instruction_message = f"{key_message_text}{texts.INSTRUCTION_MACOS}"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=buttons.DOWNLOAD_MACOS_BUTTON, url=DOWNLOAD_MACOS))
    builder.row(
        InlineKeyboardButton(text=buttons.CONNECT_MACOS_BUTTON, url=f"{CONNECT_MACOS}{key}")
    )
    builder.row(InlineKeyboardButton(text=buttons.SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data=f"connect_pc|{key_name}"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=instruction_message,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_tv|"))
async def process_connect_tv(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    key_name = callback_query.data.split("|")[1]

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=buttons.TV_CONTINUE, callback_data=f"continue_tv|{key_name}")
    )
    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data=f"view_key|{key_name}"))
    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=texts.CONNECT_TV_TEXT,
        reply_markup=builder.as_markup(),
        media_path=None,
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("continue_tv|"))
async def process_continue_tv(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    key_name = callback_query.data.split("|")[1]
    record = await get_key_details(session, key_name)
    subscription_link = record.get("key") or record.get("remnawave_link")
    message_text = texts.SUBSCRIPTION_DETAILS_TEXT.format(subscription_link=subscription_link)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data=f"connect_tv|{key_name}"))
    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=message_text,
        reply_markup=builder.as_markup(),
        media_path=None,
    )