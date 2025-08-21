import os

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    BALANCE_BUTTON,
    GIFT_BUTTON,
    INSTRUCTIONS_BUTTON,
    NEWS_MESSAGE,
    REFERRAL_BUTTON,
    SHOW_START_MENU_ONCE,
    TRIAL_TIME_DISABLE,
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
    RENEW_KEY,
    TRIAL_SUB,
)
from handlers.texts import ADD_SUBSCRIPTION_HINT
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks

from .admin.panel.keyboard import AdminPanelCallback
from .texts import profile_message_send
from .utils import edit_or_send_message, get_username


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
        user = callback_query_or_message.from_user
        message = callback_query_or_message.message
    else:
        chat = callback_query_or_message.chat
        user = callback_query_or_message.from_user
        message = callback_query_or_message

    chat_id = chat.id
    username = get_username(user or chat)

    key_count = await get_key_count(session, chat_id)
    balance = await get_balance(session, chat_id) or 0
    trial_status = await get_trial(session, chat_id)

    profile_message = profile_message_send(username, chat_id, int(balance), key_count)
    profile_message += ADD_SUBSCRIPTION_HINT if key_count == 0 else f"\n<blockquote><i>{NEWS_MESSAGE}</i></blockquote>"

    text_hooks = await run_hooks("profile_text", username=username, chat_id=chat_id, balance=int(balance), key_count=key_count, session=session)
    if text_hooks:
        profile_message = text_hooks[0]

    builder = InlineKeyboardBuilder()

    if key_count > 0:
        builder.row(InlineKeyboardButton(text=RENEW_KEY, callback_data="renew_menu"))
        builder.row(InlineKeyboardButton(text=MY_SUBS, callback_data="view_keys"))
    elif trial_status == 0 and not TRIAL_TIME_DISABLE:
        builder.row(InlineKeyboardButton(text=TRIAL_SUB, callback_data="create_key"))
    else:
        builder.row(InlineKeyboardButton(text=ADD_SUB, callback_data="create_key"))

    if BALANCE_BUTTON:
        builder.row(InlineKeyboardButton(text=BALANCE, callback_data="balance"))

    extra_buttons = []
    if REFERRAL_BUTTON:
        extra_buttons.append(InlineKeyboardButton(text=INVITE, callback_data="invite"))
    if GIFT_BUTTON:
        extra_buttons.append(InlineKeyboardButton(text=GIFTS, callback_data="gifts"))
    if extra_buttons:
        builder.row(*extra_buttons)

    module_buttons = await run_hooks("profile_menu", chat_id=chat_id, admin=admin, session=session)
    builder = insert_hook_buttons(builder, module_buttons)

    if INSTRUCTIONS_BUTTON:
        builder.row(InlineKeyboardButton(text=INSTRUCTIONS, callback_data="instructions"))

    if admin:
        builder.row(
            InlineKeyboardButton(text="📊 Администратор", callback_data=AdminPanelCallback(action="admin").pack())
        )

    if SHOW_START_MENU_ONCE:
        builder.row(InlineKeyboardButton(text=ABOUT_VPN, callback_data="about_vpn"))
    else:
        builder.row(InlineKeyboardButton(text=BACK, callback_data="start"))

    await edit_or_send_message(
        target_message=message,
        text=profile_message,
        reply_markup=builder.as_markup(),
        media_path=os.path.join("img", "profile.jpg"),
        disable_web_page_preview=False,
        force_text=True,
    )
