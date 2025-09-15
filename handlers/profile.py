import asyncio
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
    MY_SUB,
    MY_SUBS,
    TRIAL_SUB,
)
from handlers.payments.currency_rates import format_for_user
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

    key_count, balance_rub, trial_status = await asyncio.gather(
        get_key_count(session, chat_id),
        get_balance(session, chat_id),
        get_trial(session, chat_id),
    )
    balance_rub = balance_rub or 0

    fmt_task = asyncio.create_task(format_for_user(session, chat_id, balance_rub, getattr(user, "language_code", None)))
    profile_menu_task = asyncio.create_task(run_hooks("profile_menu", chat_id=chat_id, admin=admin, session=session))
    profile_text_task = asyncio.create_task(
        run_hooks(
            "profile_text",
            username=username,
            chat_id=chat_id,
            balance=int(balance_rub),
            key_count=key_count,
            session=session,
        )
    )

    balance_text = await fmt_task

    profile_message = profile_message_send(username, chat_id, balance_text, key_count)
    profile_message += ADD_SUBSCRIPTION_HINT if key_count == 0 else f"\n<blockquote><i>{NEWS_MESSAGE}</i></blockquote>"

    text_hooks = await profile_text_task
    if text_hooks:
        profile_message = text_hooks[0]

    builder = InlineKeyboardBuilder()

    if key_count > 0:
        subs_label = MY_SUB if key_count == 1 else MY_SUBS
        builder.row(InlineKeyboardButton(text=subs_label, callback_data="view_keys"))
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

    module_buttons = await profile_menu_task
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
