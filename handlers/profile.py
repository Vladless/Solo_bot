import html
import os

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    GIFT_BUTTON,
    INSTRUCTIONS_BUTTON,
    NEWS_MESSAGE,
    REFERRAL_BUTTON,
    SHOW_START_MENU_ONCE,
)
from database import get_balance, get_key_count, get_trial
from handlers.buttons import (
    ABOUT_VPN,
    ADD_SUB,
    BACK,
    BALANCE,
    GIFTS,
    INSTRUCTIONS,
    INVITE,
    MY_SUBS,
    TRIAL_SUB,
)
from handlers.texts import ADD_SUBSCRIPTION_HINT
from logger import logger

from .admin.panel.keyboard import AdminPanelCallback
from .texts import profile_message_send
from .utils import edit_or_send_message

router = Router()


@router.callback_query(F.data == "profile")
@router.message(F.text == "/profile")
async def process_callback_view_profile(
    callback_query_or_message: Message | CallbackQuery,
    state: FSMContext,
    admin: bool,
    session,
):
    if isinstance(callback_query_or_message, CallbackQuery):
        chat = callback_query_or_message.message.chat
        from_user = callback_query_or_message.from_user
        chat_id = chat.id
        target_message = callback_query_or_message.message
    else:
        chat = callback_query_or_message.chat
        from_user = callback_query_or_message.from_user
        chat_id = chat.id
        target_message = callback_query_or_message

    user = chat if chat.type == "private" else from_user

    if getattr(user, "full_name", None):
        username = html.escape(user.full_name)
    elif getattr(user, "first_name", None):
        username = html.escape(user.first_name)
    elif getattr(user, "username", None):
        username = "@" + html.escape(user.username)
    else:
        username = "Пользователь"

    image_path = os.path.join("img", "profile.jpg")
    logger.info(f"Переход в профиль. Используется изображение: {image_path}")

    key_count = await get_key_count(session, chat_id)
    balance = await get_balance(session, chat_id) or 0
    trial_status = await get_trial(session, chat_id)

    profile_message = profile_message_send(username, chat_id, int(balance), key_count)
    if key_count == 0:
        profile_message += ADD_SUBSCRIPTION_HINT
    else:
        profile_message += f"\n<blockquote> <i>{NEWS_MESSAGE}</i></blockquote>"

    builder = InlineKeyboardBuilder()
    if key_count > 0:
        builder.row(InlineKeyboardButton(text=MY_SUBS, callback_data="view_keys"))
    elif trial_status == 0:
        builder.row(InlineKeyboardButton(text=TRIAL_SUB, callback_data="create_key"))
    else:
        builder.row(InlineKeyboardButton(text=ADD_SUB, callback_data="create_key"))
    builder.row(InlineKeyboardButton(text=BALANCE, callback_data="balance"))

    row_buttons = []
    if REFERRAL_BUTTON:
        row_buttons.append(InlineKeyboardButton(text=INVITE, callback_data="invite"))
    if GIFT_BUTTON:
        row_buttons.append(InlineKeyboardButton(text=GIFTS, callback_data="gifts"))
    if row_buttons:
        builder.row(*row_buttons)

    if INSTRUCTIONS_BUTTON:
        builder.row(
            InlineKeyboardButton(text=INSTRUCTIONS, callback_data="instructions")
        )
    if admin:
        builder.row(
            InlineKeyboardButton(
                text="📊 Администратор",
                callback_data=AdminPanelCallback(action="admin").pack(),
            )
        )
    if SHOW_START_MENU_ONCE:
        builder.row(InlineKeyboardButton(text=ABOUT_VPN, callback_data="about_vpn"))
    else:
        builder.row(InlineKeyboardButton(text=BACK, callback_data="start"))

    await edit_or_send_message(
        target_message=target_message,
        text=profile_message,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        disable_web_page_preview=False,
        force_text=True,
    )
