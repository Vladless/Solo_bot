import os

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import (
    CAPTCHA_ENABLE,
    CHANNEL_EXISTS,
    CHANNEL_ID,
    CHANNEL_REQUIRED,
    CHANNEL_URL,
    DONATIONS_ENABLE,
    SHOW_START_MENU_ONCE,
    SUPPORT_CHAT_URL,
)
from database import (
    add_user,
    check_user_exists,
    get_trial,
)
from handlers.buttons import ABOUT_VPN, BACK, CHANNEL, MAIN_MENU, SUPPORT, TRIAL_SUB
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
from logger import logger

from .admin.panel.keyboard import AdminPanelCallback
from .refferal import handle_referral_link
from .utils import edit_or_send_message


router = Router()


@router.callback_query(F.data == "start")
async def handle_start_callback_query(
    callback_query: CallbackQuery, state: FSMContext, session: Any, admin: bool, captcha: bool = False
):
    await start_command(callback_query.message, state, session, admin, captcha)


@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext, session: Any, admin: bool, captcha: bool = True):
    logger.info(f"Вызвана функция start_command для пользователя {message.chat.id}")

    if CAPTCHA_ENABLE and captcha:
        user_exists = await check_user_exists(message.chat.id)
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

    if CHANNEL_EXISTS and CHANNEL_REQUIRED:
        try:
            member = await bot.get_chat_member(CHANNEL_ID, message.chat.id)
            if member.status not in ["member", "administrator", "creator"]:
                await state.update_data(original_text=text_to_process)
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription"))
                await edit_or_send_message(
                    target_message=message,
                    text=SUBSCRIPTION_REQUIRED_MSG,
                    reply_markup=builder.as_markup(),
                )
                return
            else:
                logger.info(
                    f"Пользователь {message.chat.id} подписан на канал (статус: {member.status}). Продолжаем работу."
                )
        except Exception as e:
            logger.error(f"Ошибка проверки подписки пользователя {message.chat.id}: {e}")
            await state.update_data(start_text=text_to_process)
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription"))
            await edit_or_send_message(
                target_message=message,
                text=SUBSCRIPTION_REQUIRED_MSG,
                reply_markup=builder.as_markup(),
            )
            return
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
            builder.row(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription"))
            await callback_query.message.edit_text(
                SUBSCRIPTION_REQUIRED_MSG,
                reply_markup=builder.as_markup(),
            )
        else:
            await callback_query.answer(SUBSCRIPTION_CONFIRMED_MSG)
            data = await state.get_data()
            original_text = data.get("original_text")
            if not original_text:
                original_text = callback_query.message.text
            await process_start_logic(callback_query.message, state, session, admin, text_to_process=original_text)
            logger.info(f"[CALLBACK] Завершен вызов process_start_logic для пользователя {user_id}")
    except Exception as e:
        logger.error(f"[CALLBACK] Ошибка проверки подписки для пользователя {user_id}: {e}", exc_info=True)
        await callback_query.answer(SUBSCRIPTION_CHECK_ERROR_MSG, show_alert=True)


async def process_start_logic(
    message: Message, state: FSMContext, session: Any, admin: bool, text_to_process: str = None
):
    text = text_to_process or message.text or message.caption

    if not text:
        logger.info(f"[StartLogic] Текста нет — вызываю стартовое меню для {message.chat.id}")
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
            if "coupons" in part:
                logger.info(f"Обнаружена ссылка на купон: {part}")
                coupon_code = part.split("coupons")[1].strip("_")
                await activate_coupon(message, state, session, coupon_code=coupon_code, admin=admin)
                continue

            if "gift" in part:
                gift_raw = part.split("gift")[1].strip("_")
                parts = gift_raw.split("_")
                if len(parts) < 2:
                    await message.answer("❌ Неверный формат ссылки на подарок.")
                    return await process_callback_view_profile(message, state, admin)

                gift_id = parts[0]
                sender_id = parts[1]
                logger.info(f"[GIFT] Обнаружен подарок {gift_id} от {sender_id}")
                await handle_gift_link(gift_id, message, state, session)
                gift_detected = True
                break

            if "referral" in part:
                referrer_tg_id = part.split("referral")[1].strip("_")
                try:
                    referrer_tg_id = int(referrer_tg_id)
                    await handle_referral_link(referrer_tg_id, message, state, session)
                except (ValueError, IndexError):
                    pass
                continue

            if "utm" in part:
                utm_code = part
                logger.info(f"Обнаружена ссылка на UTM: {utm_code}")
                await handle_utm_link(utm_code, message, state, session)
                continue

        if gift_detected:
            return

        user_exists = await check_user_exists(message.chat.id)
        if not user_exists:
            from_user = message.from_user
            await add_user(
                tg_id=from_user.id,
                username=from_user.username,
                first_name=from_user.first_name,
                last_name=from_user.last_name,
                language_code=from_user.language_code,
                is_bot=from_user.is_bot,
                session=session,
            )

        trial_status = await get_trial(message.chat.id, session)

        if SHOW_START_MENU_ONCE:
            if trial_status > 0:
                await process_callback_view_profile(message, state, admin)
            else:
                await show_start_menu(message, admin, session)
        else:
            await show_start_menu(message, admin, session)

    except Exception as e:
        logger.error(f"Ошибка при обработке текста {message.text} — {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


async def handle_utm_link(utm_code, message, state, session):
    exists = await session.fetchval("SELECT 1 FROM tracking_sources WHERE code = $1", utm_code)
    if exists:
        current_code = await session.fetchval("SELECT source_code FROM users WHERE tg_id = $1", message.chat.id)
        if current_code is None:
            user_exists = await check_user_exists(message.chat.id)
            from_user = message.from_user
            if not user_exists:
                await add_user(
                    tg_id=from_user.id,
                    username=from_user.username,
                    first_name=from_user.first_name,
                    last_name=from_user.last_name,
                    language_code=from_user.language_code,
                    is_bot=from_user.is_bot,
                    session=session,
                    source_code=utm_code,
                )
            else:
                await session.execute("UPDATE users SET source_code = $1 WHERE tg_id = $2", utm_code, message.chat.id)
        logger.info(f"[UTM] Привязана {utm_code} к пользователю {message.chat.id}")
    else:
        await message.answer("❌ UTM ссылка не найдена.")


async def show_start_menu(message: Message, admin: bool, session: Any):
    """Функция для отображения стандартного меню через редактирование сообщения.
    Если редактирование не удалось, отправляем новое сообщение."""
    logger.info(f"Показываю главное меню для пользователя {message.chat.id}")

    image_path = os.path.join("img", "pic.jpg")
    builder = InlineKeyboardBuilder()

    if session is not None:
        trial_status = await get_trial(message.chat.id, session)
        logger.info(f"Trial status для {message.chat.id}: {trial_status}")
        if trial_status == 0:
            builder.row(InlineKeyboardButton(text=TRIAL_SUB, callback_data="create_key"))
        else:
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    else:
        logger.warning(f"Сессия базы данных отсутствует, пропускаем проверку триала для {message.chat.id}")

    if not SHOW_START_MENU_ONCE:
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
            InlineKeyboardButton(text="📊 Администратор", callback_data=AdminPanelCallback(action="admin").pack())
        )

    builder.row(InlineKeyboardButton(text=ABOUT_VPN, callback_data="about_vpn"))

    await edit_or_send_message(
        target_message=message,
        text=WELCOME_TEXT,
        reply_markup=builder.as_markup(),
        media_path=image_path,
    )


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()
    if DONATIONS_ENABLE:
        builder.row(InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate"))
    support_btn = InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL)
    if CHANNEL_EXISTS:
        channel_btn = InlineKeyboardButton(text=CHANNEL, url=CHANNEL_URL)
        builder.row(support_btn, channel_btn)
    else:
        builder.row(support_btn)

    builder.row(InlineKeyboardButton(text=BACK, callback_data="start"))
    text = get_about_vpn("3.2.3-minor")
    image_path = os.path.join("img", "pic.jpg")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        force_text=False,
    )
