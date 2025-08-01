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
    check_user_exists,
    get_coupon_by_code,
    get_key_count,
    get_trial,
)
from database.models import TrackingSource, User
from handlers.buttons import (
    ABOUT_VPN,
    BACK,
    CHANNEL,
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
from hooks.hooks import run_hooks
from logger import logger

from .admin.panel.keyboard import AdminPanelCallback
from .refferal import handle_referral_link
from .utils import edit_or_send_message


router = Router()

processing_gifts = set()


@router.callback_query(F.data == "start")
async def handle_start_callback_query(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: Any,
    admin: bool,
    captcha: bool = False,
):
    await start_command(callback_query.message, state, session, admin, captcha)


@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext, session: Any, admin: bool, captcha: bool = True):
    logger.info(f"Вызвана функция start_command для пользователя {message.chat.id}")

    if CAPTCHA_ENABLE and captcha:
        user_exists = await check_user_exists(session, message.chat.id)
        if not user_exists:
            captcha_data = await generate_captcha(message, state)
            await edit_or_send_message(
                target_message=message,
                text=captcha_data["text"],
                reply_markup=captcha_data["markup"],
            )
            return

    state_data = await state.get_data()
    text_to_process = state_data.get("original_text", message.text)
    await process_start_logic(message, state, session, admin, text_to_process)


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback_query: CallbackQuery, state: FSMContext, session: Any, admin: bool):
    user_id = callback_query.from_user.id
    logger.info(f"[CALLBACK] Получен callback 'check_subscription' от пользователя {user_id}")

    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        logger.info(f"[CALLBACK] Статус подписки пользователя {user_id}: {member.status}")

        if member.status not in ["member", "administrator", "creator"]:
            await callback_query.answer(NOT_SUBSCRIBED_YET_MSG, show_alert=True)
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=SUB_CHANELL, url=CHANNEL_URL))
            builder.row(InlineKeyboardButton(text=SUB_CHANELL_DONE, callback_data="check_subscription"))
            await callback_query.message.edit_text(
                SUBSCRIPTION_REQUIRED_MSG,
                reply_markup=builder.as_markup(),
            )
            return
        await callback_query.answer(SUBSCRIPTION_CONFIRMED_MSG)
        data = await state.get_data()
        original_text = data.get("original_text") or callback_query.message.text
        user_data = data.get("user_data")

        if not user_data:
            user = callback_query.from_user
            user_data = {
                "tg_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language_code": user.language_code,
                "is_bot": user.is_bot,
            }
            await state.update_data(user_data=user_data)

        if user_data.get("is_bot"):
            logger.warning(f"[CALLBACK] Попытка регистрации бота: {user_data}")
            return

        await process_start_logic(
            message=callback_query.message,
            state=state,
            session=session,
            admin=admin,
            text_to_process=original_text,
            user_data=user_data,
        )
        logger.info(f"[CALLBACK] Завершен вызов process_start_logic для пользователя {user_id}")

    except Exception as e:
        logger.error(f"[CALLBACK] Ошибка проверки подписки для пользователя {user_id}: {e}", exc_info=True)
        await callback_query.answer(SUBSCRIPTION_CHECK_ERROR_MSG, show_alert=True)


async def process_start_logic(
    message: Message,
    state: FSMContext,
    session: Any,
    admin: bool,
    text_to_process: str = None,
    user_data: dict | None = None,
):
    user_data = user_data or {
        "tg_id": (message.from_user or message.chat).id,
        "username": getattr(message.from_user, "username", None),
        "first_name": getattr(message.from_user, "first_name", None),
        "last_name": getattr(message.from_user, "last_name", None),
        "language_code": getattr(message.from_user, "language_code", None),
        "is_bot": getattr(message.from_user, "is_bot", False),
    }

    text = text_to_process or message.text or message.caption

    if not text:
        logger.info(f"[StartLogic] Текста нет — вызываю стартовое меню для {user_data['tg_id']}")
        await show_start_menu(message, admin, session)
        return

    if text.startswith("/start "):
        parts = text.split(maxsplit=1)
        if len(parts) > 1:
            text = parts[1]

    try:
        gift_detected = False
        text_parts = text.split("-")

        for part in text_parts:
            await run_hooks("start_link", message=message, state=state, session=session, user_data=user_data, part=part)

        for part in text_parts:
            if "coupons" in part:
                logger.info(f"Обнаружена ссылка на купон: {part}")
                coupon_code = part.split("coupons")[1].strip("_")
                coupon = await get_coupon_by_code(session, coupon_code)
                if not coupon:
                    continue

                await activate_coupon(
                    message,
                    state,
                    session,
                    coupon_code,
                    admin=admin,
                    user_data=user_data,
                )

                if coupon.days:
                    return
                continue

            if "gift" in part:
                gift_raw = part.split("gift")[1].strip("_")
                
                if not gift_raw:
                    await message.answer("❌ Неверный формат ссылки на подарок.")
                    return await process_callback_view_profile(message, state, admin, session)

                gift_id = gift_raw

                if gift_id in processing_gifts:
                    await message.answer("⏳ Подарок уже обрабатывается, подождите...")
                    return await process_callback_view_profile(message, state, admin, session)

                processing_gifts.add(gift_id)

                try:
                    logger.info(f"[GIFT] Обнаружен подарок {gift_id}")
                    await handle_gift_link(gift_id, message, state, session, user_data=user_data)
                    gift_detected = True
                finally:
                    processing_gifts.discard(gift_id)

                break

            if "referral" in part:
                referrer_tg_id = part.split("referral")[1].strip("_")
                try:
                    referrer_tg_id = int(referrer_tg_id)
                    await handle_referral_link(referrer_tg_id, message, state, session, user_data=user_data)
                except (ValueError, IndexError):
                    pass
                continue

            if "utm" in part:
                utm_code = part
                logger.info(f"[UTM] Обнаружена ссылка на UTM: {utm_code}")
                await handle_utm_link(utm_code, message, state, session, user_data=user_data)
                continue

        await state.clear()
        if gift_detected:
            return

        user_exists = await check_user_exists(session, user_data["tg_id"])
        if not user_exists:
            await add_user(session=session, **user_data)

        trial_status = await get_trial(session, user_data["tg_id"])
        key_count = await get_key_count(session, user_data["tg_id"])

        if SHOW_START_MENU_ONCE:
            if key_count > 0:
                await process_callback_view_profile(message, state, admin, session)
            elif trial_status == 0:
                await show_start_menu(message, admin, session)
            else:
                await process_callback_view_profile(message, state, admin, session)
        else:
            await show_start_menu(message, admin, session)

        await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при обработке текста {text} — {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


async def handle_utm_link(
    utm_code: str,
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user_data: dict,
):
    user_id = user_data["tg_id"]

    result = await session.execute(select(TrackingSource).where(TrackingSource.code == utm_code))
    utm_exists = result.scalar_one_or_none()

    if not utm_exists:
        await message.answer("❌ UTM ссылка не найдена.")
        return
    result = await session.execute(select(User).where(User.tg_id == user_id))
    user = result.scalar_one_or_none()

    if user and user.source_code is None:
        user.source_code = utm_code
        await session.commit()
        logger.info(f"[UTM] Привязана {utm_code} к пользователю {user_id}")
    elif not user:
        await add_user(session=session, source_code=utm_code, **user_data)
        logger.info(f"[UTM] Зарегистрирован и привязан {utm_code} к пользователю {user_id}")


async def show_start_menu(message: Message, admin: bool, session: AsyncSession):
    """Функция для отображения стандартного меню через редактирование сообщения."""

    image_path = os.path.join("img", "pic.jpg")
    builder = InlineKeyboardBuilder()

    trial_status = None
    if session is not None:
        trial_status = await get_trial(session, message.chat.id)
    else:
        logger.warning(f"Сессия базы данных отсутствует, пропускаем проверку триала для {message.chat.id}")

    show_trial_button = trial_status == 0 and not TRIAL_TIME_DISABLE
    show_profile_button = not SHOW_START_MENU_ONCE or trial_status != 0 or TRIAL_TIME_DISABLE

    if show_trial_button:
        builder.row(InlineKeyboardButton(text=TRIAL_SUB, callback_data="create_key"))
    if show_profile_button:
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if CHANNEL_EXISTS:
        builder.row(
            InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL),
            InlineKeyboardButton(text=CHANNEL, url=CHANNEL_URL),
        )
    else:
        builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))

    if admin:
        builder.row(
            InlineKeyboardButton(
                text="📊 Администратор",
                callback_data=AdminPanelCallback(action="admin").pack(),
            )
        )

    builder.row(InlineKeyboardButton(text=ABOUT_VPN, callback_data="about_vpn"))

    await edit_or_send_message(
        target_message=message,
        text=WELCOME_TEXT,
        reply_markup=builder.as_markup(),
        media_path=image_path,
    )


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback_query: CallbackQuery, session: AsyncSession):
    user_id = callback_query.from_user.id
    trial = await get_trial(session, user_id)

    back_target = "profile" if SHOW_START_MENU_ONCE and trial > 0 else "start"

    builder = InlineKeyboardBuilder()
    if DONATIONS_ENABLE:
        builder.row(InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate"))

    support_btn = InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL)
    if CHANNEL_EXISTS:
        channel_btn = InlineKeyboardButton(text=CHANNEL, url=CHANNEL_URL)
        builder.row(support_btn, channel_btn)
    else:
        builder.row(support_btn)

    builder.row(InlineKeyboardButton(text=BACK, callback_data=back_target))

    text = get_about_vpn("3.2.3-minor")
    image_path = os.path.join("img", "pic.jpg")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        force_text=False,
    )
