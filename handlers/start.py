import os

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
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
from database import (
    add_user,
    get_coupon_by_code,
    get_user_snapshot,
    upsert_source_if_empty,
)
from database.models import TrackingSource
from handlers.buttons import (
    ABOUT_VPN,
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
from handlers.payments.gift import handle_gift_link
from handlers.profile import process_callback_view_profile
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

from .admin.panel.keyboard import AdminPanelCallback
from .refferal import handle_referral_link
from .utils import edit_or_send_message, extract_user_data


router = Router()
processing_gifts = set()


@router.message(Command("start"))
@router.callback_query(F.data == "start")
async def start_entry(
    event: Message | CallbackQuery, state: FSMContext, session: Any, admin: bool, captcha: bool = True
):
    message = event.message if isinstance(event, CallbackQuery) else event
    if CAPTCHA_ENABLE and captcha:
        exists = await get_user_snapshot(session, message.chat.id)
        if exists is None:
            captcha_data = await generate_captcha(message, state)
            await edit_or_send_message(message, captcha_data["text"], reply_markup=captcha_data["markup"])
            return
    text = getattr(event, "data", None) or message.text
    await process_start_logic(message, state, session, admin, text)


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery, state: FSMContext, session: Any, admin: bool):
    user_id = callback.from_user.id
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        if member.status not in ["member", "administrator", "creator"]:
            await prompt_subscription(callback)
            return
        await callback.answer(SUBSCRIPTION_CONFIRMED_MSG)
        data = await state.get_data()
        original_text = data.get("original_text") or callback.message.text
        user_data = data.get("user_data") or extract_user_data(callback.from_user)
        await state.update_data(user_data=user_data)
        await process_start_logic(callback.message, state, session, admin, original_text, user_data)
    except Exception as e:
        logger.error(f"[CALLBACK] Ошибка подписки: {e}", exc_info=True)
        await callback.answer(SUBSCRIPTION_CHECK_ERROR_MSG, show_alert=True)


async def process_start_logic(
    message: Message,
    state: FSMContext,
    session: Any,
    admin: bool,
    text_to_process: str = None,
    user_data: dict | None = None,
):
    user_data = user_data or extract_user_data(message.from_user or message.chat)
    text = text_to_process or message.text or message.caption
    if not text:
        trial_key = await get_user_snapshot(session, user_data["tg_id"])
        trial = 0
        key_count = 0
        if trial_key is not None:
            trial, key_count = trial_key
        await show_start_menu(message, admin, session, trial=trial, key_count=key_count)
        return

    if text.startswith("/start "):
        text = text.split(maxsplit=1)[1]

    await state.update_data(original_text=text, user_data=user_data)

    gift_detected = False
    for part in text.split("-"):
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

    await state.clear()
    if gift_detected:
        return

    await add_user(session=session, **user_data)

    trial_key = await get_user_snapshot(session, user_data["tg_id"])
    trial = 0
    key_count = 0
    if trial_key is not None:
        trial, key_count = trial_key

    if SHOW_START_MENU_ONCE:
        if key_count > 0 or trial == 1:
            await process_callback_view_profile(message, state, admin, session)
        else:
            await show_start_menu(message, admin, session, trial=trial, key_count=key_count)
    else:
        await show_start_menu(message, admin, session, trial=trial, key_count=key_count)


async def handle_coupon_link(part, message, state, session, admin, user_data):
    code = part.split("coupons")[1].strip("_")
    coupon = await get_coupon_by_code(session, code)
    if coupon:
        await activate_coupon(message, state, session, code, admin=admin, user_data=user_data)
        if getattr(coupon, "days", None):
            return


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
        await handle_referral_link(referrer_id, message, state, session, user_data)
    except Exception:
        pass


async def prompt_subscription(callback: CallbackQuery):
    await callback.answer(NOT_SUBSCRIBED_YET_MSG, show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=SUB_CHANELL, url=CHANNEL_URL))
    kb.row(InlineKeyboardButton(text=SUB_CHANELL_DONE, callback_data="check_subscription"))
    await callback.message.edit_text(SUBSCRIPTION_REQUIRED_MSG, reply_markup=kb.as_markup())


async def handle_utm_link(utm_code: str, message: Message, state: FSMContext, session: AsyncSession, user_data: dict):
    res = await session.execute(select(TrackingSource).where(TrackingSource.code == utm_code))
    if not res.scalar_one_or_none():
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

    show_trial = (trial_status in (-1, 0)) and (not TRIAL_TIME_DISABLE) and (key_cnt == 0)
    show_profile = (key_cnt > 0) or (
        ((not SHOW_START_MENU_ONCE) or (trial_status not in (-1, 0)) or TRIAL_TIME_DISABLE) and (not show_trial)
    )

    if show_trial:
        kb.row(InlineKeyboardButton(text=TRIAL_SUB, callback_data="create_key"))
    if show_profile:
        kb.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if CHANNEL_EXISTS:
        kb.row(
            InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL),
            InlineKeyboardButton(text=CHANNEL, url=CHANNEL_URL),
        )
    else:
        kb.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))

    if admin:
        kb.row(InlineKeyboardButton(text="📊 Администратор", callback_data=AdminPanelCallback(action="admin").pack()))

    try:
        module_buttons = await run_hooks("start_menu", chat_id=message.chat.id, session=session)
        kb = insert_hook_buttons(kb, module_buttons)
    except Exception as e:
        logger.error(f"[Hooks:start_menu] Ошибка вставки кнопок: {e}")

    kb.row(InlineKeyboardButton(text=ABOUT_VPN, callback_data="about_vpn"))

    await edit_or_send_message(message, WELCOME_TEXT, reply_markup=kb.as_markup(), media_path=image_path)


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback: CallbackQuery, session: AsyncSession):
    user_id = callback.from_user.id
    snap = await get_user_snapshot(session, user_id)
    trial = 0 if snap is None else snap[0]
    back_target = "profile" if SHOW_START_MENU_ONCE and trial > 0 else "start"

    kb = InlineKeyboardBuilder()
    if DONATIONS_ENABLE:
        kb.row(InlineKeyboardButton(text=DONAT_BUTTON, callback_data="donate"))

    kb.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    if CHANNEL_EXISTS:
        kb.row(InlineKeyboardButton(text=CHANNEL, url=CHANNEL_URL))

    module_buttons = await run_hooks("about_menu", chat_id=user_id, trial=trial, session=session)
    kb = insert_hook_buttons(kb, module_buttons)

    kb.row(InlineKeyboardButton(text=BACK, callback_data=back_target))

    text = get_about_vpn("3.2.3-minor")
    text_hooks = await run_hooks("about_text", chat_id=user_id, trial=trial, session=session)
    if text_hooks:
        text = text_hooks[0]

    await edit_or_send_message(
        callback.message, text, reply_markup=kb.as_markup(), media_path=os.path.join("img", "pic.jpg"), force_text=False
    )
