import os

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from config import (
    CAPTCHA_ENABLE,
    CHANNEL_EXISTS,
    CHANNEL_ID,
    CHANNEL_URL,
    DONATIONS_ENABLE,
    SHOW_START_MENU_ONCE,
    SUPPORT_CHAT_URL,
    TRIAL_TIME_DISABLE,
)
from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG
from core.cache_config import START_UTM_EXISTS_TTL_SEC
from core.redis_cache import cache_get, cache_key, cache_set
from database import (
    add_user,
    get_user_snapshot,
    upsert_source_if_empty,
)
from database.models import TrackingSource
from handlers.buttons import (
    ABOUT_VPN,
    ADMIN_BTN,
    BACK,
    CHANNEL,
    DONAT_BUTTON,
    MAIN_MENU,
    SUB_CHANELL,
    SUB_CHANELL_DONE,
    SUPPORT,
    TRIAL_SUB,
)
from handlers.captcha import generate_captcha
from handlers.coupons import activate_coupon
from handlers.instructions.instructions import send_instructions
from handlers.keys.key_create import confirm_create_new_key
from handlers.keys.key_view import process_callback_or_message_view_keys
from handlers.payments.gift import handle_gift_link
from handlers.profile import process_callback_view_profile
from handlers.refferal import invite_handler
from handlers.texts import (
    NOT_SUBSCRIBED_YET_MSG,
    SUBSCRIPTION_CHECK_ERROR_MSG,
    SUBSCRIPTION_CONFIRMED_MSG,
    SUBSCRIPTION_REQUIRED_MSG,
    WELCOME_TEXT,
    get_about_vpn,
)
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from logger import logger
from middlewares.session import release_session_early

from .admin.panel.keyboard import AdminPanelCallback
from .refferal import handle_referral_link
from .utils import edit_or_send_message, extract_user_data, safe_answer_callback


router = Router()
processing_gifts = set()


async def get_or_load_user_snapshot(
    session: AsyncSession,
    cached_snapshot: tuple[int, int] | None,
    tg_id: int,
) -> tuple[int, int] | None:
    """Возвращает снапшот пользователя, используя кеш если есть."""
    if cached_snapshot is not None:
        return cached_snapshot
    return await get_user_snapshot(session, tg_id)


@router.message(Command("start"))
@router.callback_query(F.data == "start")
async def start_entry(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: Any,
    admin: bool,
    captcha: bool = True,
):
    message = event.message if isinstance(event, CallbackQuery) else event
    try:
        await run_hooks("start_entry", message=message, event=event, state=state, session=session, admin=admin)
    except Exception as e:
        logger.error(f"[Hooks:start_entry] Ошибка: {e}", exc_info=True)

    user_snapshot = None

    captcha_enabled = bool(MODES_CONFIG.get("CAPTCHA_ENABLED", CAPTCHA_ENABLE))
    if captcha_enabled and captcha:
        user_snapshot = await get_user_snapshot(session, message.chat.id)
        if user_snapshot is None:
            captcha_data = await generate_captcha(message, state)
            await edit_or_send_message(message, captcha_data["text"], reply_markup=captcha_data["markup"])
            return

    text = getattr(event, "data", None) or message.text

    user_data = None
    if isinstance(event, CallbackQuery):
        user_data = extract_user_data(event.from_user)

    await process_start_logic(message, state, session, admin, text, user_data, user_snapshot=user_snapshot)


@router.callback_query(F.data == "check_subscription", flags={"popup": True})
async def check_subscription_callback(callback: CallbackQuery, state: FSMContext, session: Any, admin: bool):
    user_id = callback.from_user.id
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status not in ["member", "administrator", "creator"]:
            await prompt_subscription(callback)
            return
        await safe_answer_callback(callback, SUBSCRIPTION_CONFIRMED_MSG)
        data = await state.get_data()
        original_text = data.get("original_text") or callback.message.text
        user_data = data.get("user_data") or extract_user_data(callback.from_user)
        await state.update_data(user_data=user_data)
        await process_start_logic(callback.message, state, session, admin, original_text, user_data)
    except Exception as e:
        logger.error(f"[CALLBACK] Ошибка подписки: {e}", exc_info=True)
        await safe_answer_callback(callback, SUBSCRIPTION_CHECK_ERROR_MSG, show_alert=True)


async def process_start_logic(
    message: Message,
    state: FSMContext,
    session: Any,
    admin: bool,
    text_to_process: str | None = None,
    user_data: dict | None = None,
    user_snapshot: tuple[int, int] | None = None,
):
    user_data = user_data or extract_user_data(message.from_user or message.chat)
    text = text_to_process or message.text or message.caption

    if text and text.startswith("/start "):
        text = text.split(maxsplit=1)[1]

    await state.update_data(original_text=text, user_data=user_data)

    if admin and text:
        _sup = text.strip().lower()
        if _sup.startswith("suser_") and _sup[6:].isdigit():
            from handlers.admin.users.users_manage import process_user_search

            await state.clear()
            await process_user_search(
                message,
                state,
                session,
                int(_sup[6:]),
                actor_tg_id=(message.from_user.id if message.from_user else None),
            )
            return

    _MAX_START_PAYLOAD_LEN = 256
    _MAX_START_PARTS = 20
    if text and len(text) > _MAX_START_PAYLOAD_LEN:
        text = text[:_MAX_START_PAYLOAD_LEN]
    parts = text.split("-") if text else []
    if len(parts) > _MAX_START_PARTS:
        parts = parts[:_MAX_START_PARTS]

    gift_detected = False
    if parts:
        for part in parts:
            part = part.strip()
            if not part:
                continue
            await run_hooks("start_link", message=message, state=state, session=session, user_data=user_data, part=part)
            if "coupons" in part:
                await handle_coupon_link(part, message, state, session, admin, user_data)
                continue
            if "gift" in part:
                gift_detected = await handle_gift(part, message, state, session, user_data)
                break
            if "referral" in part:
                await handle_referral_link_safe(part, message, state, session, user_data)
                continue
            if "utm" in part:
                await handle_utm_link(part, message, state, session, user_data)

    text = "-".join(parts) if parts else (text or "")

    await state.clear()
    if gift_detected:
        return

    await add_user(session=session, **user_data)

    tl = (text or "").strip().lower()
    if tl == "trial":
        await confirm_create_new_key(message, state, session)
        return
    if tl == "profile":
        await process_callback_view_profile(message, state, admin, session)
        return
    if tl == "buy":
        await confirm_create_new_key(message, state, session)
        return
    if tl == "subs":
        await process_callback_or_message_view_keys(message, session)
        return
    if tl == "invite":
        await invite_handler(message, session)
        return
    if tl == "instructions":
        await send_instructions(message)
        return
    if tl.startswith("tab_"):
        if await handle_cabinet_tab_link(message, tl[4:]):
            return

    trial_key = await get_or_load_user_snapshot(session, user_snapshot, user_data["tg_id"])
    trial = 0
    key_count = 0
    if trial_key is not None:
        trial, key_count = trial_key

    show_start_menu_once = bool(MODES_CONFIG.get("SHOW_START_MENU_ONLY_ONCE", SHOW_START_MENU_ONCE))

    if show_start_menu_once:
        if key_count > 0 or trial == 1:
            await process_callback_view_profile(message, state, admin, session)
        else:
            await show_start_menu(message, admin, session, trial=trial, key_count=key_count)
    else:
        await show_start_menu(message, admin, session, trial=trial, key_count=key_count)


_CABINET_TABS = {"profile", "keys", "instructions", "referrals", "partners", "gifts", "notifications"}


async def handle_cabinet_tab_link(message, tab):
    if tab not in _CABINET_TABS:
        return False
    from core.settings.web_config import get_site_url, is_web_enabled, is_web_open_in_browser

    if not is_web_enabled():
        return False
    site_url = get_site_url()
    if not site_url:
        return False
    builder = InlineKeyboardBuilder()
    if is_web_open_in_browser():
        button = InlineKeyboardButton(text="🌐 Личный кабинет", url=f"{site_url}/dashboard?tab={tab}")
    else:
        button = InlineKeyboardButton(
            text="🌐 Личный кабинет",
            web_app=WebAppInfo(url=f"{site_url}/dashboard?tab={tab}&webapp=1"),
        )
    builder.row(button)
    await message.answer(WELCOME_TEXT, reply_markup=builder.as_markup())
    return True


async def handle_coupon_link(part, message, state, session, admin, user_data):
    code = part.split("coupons")[1].strip("_")
    await activate_coupon(message, state, session, code, admin=admin, user_data=user_data)


async def handle_gift(part, message, state, session, user_data):
    gift_id = part.split("gift")[1].strip("_")
    if not gift_id:
        await message.answer("❌ Неверный формат ссылки на подарок.")
        await process_callback_view_profile(message, state, False, session)
        return False
    if gift_id in processing_gifts:
        await message.answer("⏳ Подарок уже обрабатывается, подождите...")
        await process_callback_view_profile(message, state, False, session)
        return False
    processing_gifts.add(gift_id)
    try:
        gift_results = await run_hooks(
            "gift_activation", gift_id=gift_id, message=message, state=state, session=session, user_data=user_data
        )
        if gift_results and "SUCCESS" in gift_results:
            return True
        await handle_gift_link(gift_id, message, state, session, user_data=user_data)
        return True
    finally:
        processing_gifts.discard(gift_id)


async def handle_referral_link_safe(part, message, state, session, user_data):
    try:
        referrer_id = int(part.split("referral")[1].strip("_"))
        results = await run_hooks(
            "referral_link",
            referrer_id=referrer_id,
            message=message,
            state=state,
            session=session,
            user_data=user_data,
        )
        if results and "HANDLED" in results:
            return
        await handle_referral_link(referrer_id, message, state, session, user_data)
    except Exception as e:
        logger.warning("[Referral] Ошибка обработки реферальной ссылки '{}': {}", part, e)


async def prompt_subscription(callback: CallbackQuery):
    await safe_answer_callback(callback, NOT_SUBSCRIBED_YET_MSG, show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=SUB_CHANELL, url=CHANNEL_URL))
    kb.row(InlineKeyboardButton(text=SUB_CHANELL_DONE, callback_data="check_subscription"))
    await callback.message.edit_text(SUBSCRIPTION_REQUIRED_MSG, reply_markup=kb.as_markup())


async def handle_utm_link(utm_code: str, message: Message, state: FSMContext, session: AsyncSession, user_data: dict):
    key = cache_key("utm_exists", utm_code)
    is_known = await cache_get(key)
    if is_known is None:
        stmt = select(1).select_from(TrackingSource).where(TrackingSource.code == utm_code).limit(1)
        res = await session.execute(stmt)
        is_known = res.scalar_one_or_none() is not None
        await cache_set(key, bool(is_known), START_UTM_EXISTS_TTL_SEC)

    if not is_known:
        await message.answer("❌ UTM ссылка не найдена.")
        return
    await upsert_source_if_empty(session, user_data["tg_id"], utm_code)


async def show_start_menu(
    message: Message,
    admin: bool,
    session: AsyncSession,
    trial: int | None = None,
    key_count: int | None = None,
):
    image_path = os.path.join("img", "pic.jpg")
    kb = InlineKeyboardBuilder()

    if trial is None or key_count is None:
        snap = await get_user_snapshot(session, message.chat.id)
        if snap is None:
            trial_status = 0
            key_cnt = 0
        else:
            trial_status, key_cnt = snap
    else:
        trial_status = trial
        key_cnt = key_count or 0

    trial_time_disable = bool(MODES_CONFIG.get("TRIAL_TIME_DISABLED", TRIAL_TIME_DISABLE))

    show_trial = (trial_status in (-1, 0)) and (not trial_time_disable) and (key_cnt == 0)
    show_profile = (key_cnt > 0) or (
        (
            (not bool(MODES_CONFIG.get("SHOW_START_MENU_ONLY_ONCE", SHOW_START_MENU_ONCE)))
            or (trial_status not in (-1, 0))
            or trial_time_disable
        )
        and (not show_trial)
    )

    if show_trial:
        kb.row(InlineKeyboardButton(text=TRIAL_SUB, callback_data="create_key"))
    if show_profile:
        kb.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    channel_enabled = bool(BUTTONS_CONFIG.get("CHANNEL_BUTTON_ENABLE", CHANNEL_EXISTS))
    bottom_row = []
    if SUPPORT_CHAT_URL:
        bottom_row.append(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    if channel_enabled and CHANNEL_URL:
        bottom_row.append(InlineKeyboardButton(text=CHANNEL, url=CHANNEL_URL))
    if bottom_row:
        kb.row(*bottom_row)

    if admin:
        kb.row(InlineKeyboardButton(text=ADMIN_BTN, callback_data=AdminPanelCallback(action="admin").pack()))

    try:
        module_buttons = await run_hooks("start_menu", chat_id=message.chat.id, session=session)
        kb = insert_hook_buttons(kb, module_buttons)
    except Exception as e:
        logger.error(f"[Hooks:start_menu] Ошибка вставки кнопов: {e}", exc_info=True)

    kb.row(InlineKeyboardButton(text=ABOUT_VPN, callback_data="about_vpn"))

    await release_session_early(session)
    await edit_or_send_message(message, WELCOME_TEXT, reply_markup=kb.as_markup(), media_path=image_path)


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback: CallbackQuery, session: AsyncSession):
    user_id = callback.from_user.id
    snap = await get_user_snapshot(session, user_id)
    trial = 0 if snap is None else snap[0]
    show_start_menu_once = bool(MODES_CONFIG.get("SHOW_START_MENU_ONLY_ONCE", SHOW_START_MENU_ONCE))
    back_target = "profile" if show_start_menu_once and trial > 0 else "start"

    kb = InlineKeyboardBuilder()
    if BUTTONS_CONFIG.get("DONATIONS_BUTTON_ENABLE", DONATIONS_ENABLE):
        kb.row(InlineKeyboardButton(text=DONAT_BUTTON, callback_data="donate"))

    if MODES_CONFIG.get("SUPPORT_TICKETS_ENABLED"):
        from handlers.support_triage import TriageCallback

        kb.row(InlineKeyboardButton(text=SUPPORT, callback_data=TriageCallback(action="root").pack()))
    elif SUPPORT_CHAT_URL:
        kb.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    if BUTTONS_CONFIG.get("CHANNEL_BUTTON_ENABLE", CHANNEL_EXISTS):
        kb.row(InlineKeyboardButton(text=CHANNEL, url=CHANNEL_URL))

    module_buttons = await run_hooks("about_menu", chat_id=user_id, trial=trial, session=session)
    kb = insert_hook_buttons(kb, module_buttons)

    kb.row(InlineKeyboardButton(text=BACK, callback_data=back_target))

    text = get_about_vpn("3.2.3-minor")
    text_hooks = await run_hooks("about_text", chat_id=user_id, trial=trial, session=session)
    if text_hooks:
        text = text_hooks[0]

    await edit_or_send_message(
        callback.message,
        text,
        reply_markup=kb.as_markup(),
        media_path=os.path.join("img", "pic.jpg"),
        force_text=False,
    )
