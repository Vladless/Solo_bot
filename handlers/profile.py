import os

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG
from core.redis_cache import cache_get, cache_key, cache_set
from database import get_balance_trial_key_count
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from middlewares.session import release_session_early
from services.payments.currency_rates import format_for_user
from settings.buttons import (
    ABOUT_VPN,
    ADD_SUB,
    ADMIN_BTN,
    BACK,
    BALANCE,
    BIND_EMAIL,
    GIFTS,
    INSTRUCTIONS,
    INVITE,
    MY_SUB,
    MY_SUBS,
    TRIAL_SUB,
    WEB_CABINET,
)
from settings.cache_config import BALANCE_CACHE_TTL_SEC, KEY_COUNT_CACHE_TTL_SEC, PROFILE_DATA_CACHE_TTL_SEC
from settings.config import (
    BALANCE_BUTTON,
    CHANNEL_EXISTS,
    GIFT_BUTTON,
    INSTRUCTIONS_BUTTON,
    NEWS_MESSAGE,
    REFERRAL_BUTTON,
    SHOW_START_MENU_ONCE,
    TRIAL_TIME_DISABLE,
)
from settings.texts import ADD_SUBSCRIPTION_HINT, PROFILE_CHANNEL_LINK, PROFILE_TEXT

from .admin.panel.keyboard import AdminPanelCallback
from .menu_layout import PROFILE_MENU, arrange_menu, split_hook_buttons
from .utils import edit_or_send_message, get_username, join_blocks, render_text


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

    cached = await cache_get(cache_key("profile_data", chat_id))
    has_email = None
    if isinstance(cached, dict) and "key_count" in cached and "balance_rub" in cached and "trial_status" in cached:
        key_count = int(cached["key_count"])
        balance_rub = float(cached.get("balance_rub") or 0)
        trial_status = int(cached.get("trial_status") or 0)
        if isinstance(cached.get("has_email"), bool):
            has_email = cached["has_email"]
    else:
        balance_rub, trial_status, key_count = await get_balance_trial_key_count(session, chat_id)
        balance_rub = balance_rub or 0
        from core.settings.web_config import is_email_binding_enabled as _binding_on

        if _binding_on():
            from database.identities import get_identity_by_tg_id

            identity = await get_identity_by_tg_id(session, chat_id)
            has_email = bool(identity and identity.email)
        await cache_set(cache_key("balance", chat_id), balance_rub, BALANCE_CACHE_TTL_SEC)
        await cache_set(cache_key("key_count", chat_id), key_count, KEY_COUNT_CACHE_TTL_SEC)
        payload = {"key_count": key_count, "balance_rub": balance_rub, "trial_status": trial_status}
        if has_email is not None:
            payload["has_email"] = has_email
        await cache_set(cache_key("profile_data", chat_id), payload, PROFILE_DATA_CACHE_TTL_SEC)

    balance_text = await format_for_user(
        session,
        chat_id,
        balance_rub,
        getattr(user, "language_code", None),
    )
    profile_menu_buttons = await run_hooks("profile_menu", chat_id=chat_id, admin=admin, session=session)
    text_hooks = await run_hooks(
        "profile_text",
        username=username,
        chat_id=chat_id,
        balance=int(balance_rub),
        key_count=key_count,
        session=session,
    )

    profile_message = render_text(
        PROFILE_TEXT,
        username=username,
        tg_id=chat_id,
        balance=balance_text,
        keys=key_count,
    )
    if CHANNEL_EXISTS:
        profile_message = join_blocks(profile_message, PROFILE_CHANNEL_LINK)
    if key_count == 0:
        profile_message += ADD_SUBSCRIPTION_HINT
    else:
        profile_message += f"\n<blockquote><i>{NEWS_MESSAGE}</i></blockquote>"

    if text_hooks:
        profile_message = text_hooks[0]

    single_sub_mode = bool(MODES_CONFIG.get("SINGLE_SUBSCRIPTION_MODE", False))
    single_sub_rows = None
    if single_sub_mode and key_count == 1:
        from handlers.keys.view.payload import build_single_subscription_profile

        single_sub_payload = await build_single_subscription_profile(session, chat_id, username, balance_text)
        if single_sub_payload:
            profile_message, single_sub_rows = single_sub_payload

    builder = InlineKeyboardBuilder()

    from core.settings.web_config import (
        get_site_url,
        is_email_binding_enabled,
        is_web_enabled,
        is_web_open_in_browser,
    )

    web_button = None
    if is_web_enabled():
        site_url = get_site_url()
        if site_url:
            if is_web_open_in_browser():
                web_button = InlineKeyboardButton(text=WEB_CABINET, url=f"{site_url}/dashboard")
            else:
                web_button = InlineKeyboardButton(
                    text=WEB_CABINET, web_app=WebAppInfo(url=f"{site_url}/dashboard?webapp=1")
                )

    trial_time_disabled = bool(MODES_CONFIG.get("TRIAL_TIME_DISABLED", TRIAL_TIME_DISABLE))

    bind_email_button = None
    if is_email_binding_enabled():
        if has_email is None:
            from database.identities import get_identity_by_tg_id

            identity = await get_identity_by_tg_id(session, chat_id)
            has_email = bool(identity and identity.email)
        if not has_email:
            bind_email_button = InlineKeyboardButton(text=BIND_EMAIL, callback_data="bind_email")

    if key_count > 0:
        subscription_button = InlineKeyboardButton(
            text=MY_SUB if key_count == 1 else MY_SUBS, callback_data="view_keys"
        )
    elif trial_status == 0 and not trial_time_disabled:
        subscription_button = InlineKeyboardButton(text=TRIAL_SUB, callback_data="create_key")
    else:
        subscription_button = InlineKeyboardButton(text=ADD_SUB, callback_data="create_key")

    show_start_menu_once = bool(MODES_CONFIG.get("SHOW_START_MENU_ONLY_ONCE", SHOW_START_MENU_ONCE))
    back_button = (
        InlineKeyboardButton(text=ABOUT_VPN, callback_data="about_vpn")
        if show_start_menu_once
        else InlineKeyboardButton(text=BACK, callback_data="start")
    )

    module_buttons, module_directives = split_hook_buttons(profile_menu_buttons)

    menu_buttons = {
        "bind_email": bind_email_button,
        "web_cabinet": web_button,
        "subscription": single_sub_rows if single_sub_rows is not None else subscription_button,
        "balance": InlineKeyboardButton(text=BALANCE, callback_data="balance")
        if BUTTONS_CONFIG.get("BALANCE_BUTTON_ENABLE", BALANCE_BUTTON)
        else None,
        "gifts": InlineKeyboardButton(text=GIFTS, callback_data="gifts")
        if BUTTONS_CONFIG.get("GIFT_BUTTON_ENABLE", GIFT_BUTTON)
        else None,
        "invite": InlineKeyboardButton(text=INVITE, callback_data="invite")
        if BUTTONS_CONFIG.get("REFERRAL_BUTTON_ENABLE", REFERRAL_BUTTON)
        else None,
        "instructions": InlineKeyboardButton(text=INSTRUCTIONS, callback_data="instructions")
        if BUTTONS_CONFIG.get("INSTRUCTIONS_BUTTON_ENABLE", INSTRUCTIONS_BUTTON)
        else None,
        "admin": InlineKeyboardButton(text=ADMIN_BTN, callback_data=AdminPanelCallback(action="admin").pack())
        if admin
        else None,
        "modules": [[button] for button in module_buttons],
        "back": back_button,
    }

    for row in arrange_menu(PROFILE_MENU, menu_buttons):
        builder.row(*row)

    builder = insert_hook_buttons(builder, module_directives)

    await release_session_early(session)
    await edit_or_send_message(
        target_message=message,
        text=profile_message,
        reply_markup=builder.as_markup(),
        media_path=os.path.join("img", "profile.jpg"),
        disable_web_page_preview=False,
        force_text=True,
    )
