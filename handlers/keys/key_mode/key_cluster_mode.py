import uuid

from datetime import datetime

import pytz

from aiogram import Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, FSInputFile, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import (
    CONNECT_PHONE_BUTTON,
    RENEWAL_PRICES,
    SUPPORT_CHAT_URL,
)
from database import (
    get_key_details,
    get_trial,
    update_balance,
    update_trial,
)
from handlers.buttons import (
    CONNECT_DEVICE,
    CONNECT_PHONE,
    MAIN_MENU,
    PC_BUTTON,
    TV_BUTTON,
    SUPPORT
)
from handlers.keys.key_utils import create_key_on_cluster
from handlers.texts import (
    key_message_success,
)
from handlers.utils import edit_or_send_message, generate_random_email, get_least_loaded_cluster, is_full_remnawave_cluster
from logger import logger


router = Router()

moscow_tz = pytz.timezone("Europe/Moscow")


async def key_cluster_mode(
    tg_id: int,
    expiry_time: datetime,
    state,
    session,
    message_or_query: Message | CallbackQuery | None = None,
    plan: int = None,
):
    target_message = message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query

    while True:
        key_name = generate_random_email()
        existing_key = await get_key_details(key_name, session)
        if not existing_key:
            break

    client_id = str(uuid.uuid4())
    email = key_name.lower()
    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    try:
        least_loaded_cluster = await get_least_loaded_cluster()
        await create_key_on_cluster(
            least_loaded_cluster, tg_id, client_id, email, expiry_timestamp, plan, session
        )
        logger.info(f"[Key Creation] Ключ создан на кластере {least_loaded_cluster} для пользователя {tg_id}")

        key_record = await get_key_details(email, session)
        if not key_record:
            raise ValueError(f"Ключ не найден после создания: {email}")

        public_link = key_record.get("key")
        remnawave_link = key_record.get("remnawave_link")
        final_link = public_link or remnawave_link or ""

        data = await state.get_data() if state else {}

        if data.get("is_trial"):
            trial_status = await get_trial(tg_id, session)
            if trial_status in [0, -1]:
                await update_trial(tg_id, 1, session)

        if data.get("plan_id"):
            plan_price = RENEWAL_PRICES.get(data["plan_id"])
            await update_balance(tg_id, -plan_price, session)

        logger.info(f"[Database] Баланс обновлён для пользователя {tg_id}")

    except Exception as e:
        logger.error(f"[Error] Ошибка при создании ключа для пользователя {tg_id}: {e}")
        error_message = "❌ Произошла ошибка при создании подписки. Пожалуйста, попробуйте снова."
        if target_message:
            await edit_or_send_message(
                target_message=target_message, text=error_message, reply_markup=None, media_path=None
            )
        else:
            await bot.send_message(chat_id=tg_id, text=error_message)
        return

    builder = InlineKeyboardBuilder()

    if await is_full_remnawave_cluster(least_loaded_cluster, session):
        builder.row(
            InlineKeyboardButton(
                text=CONNECT_DEVICE,
                web_app=WebAppInfo(url=final_link),
            )
        )
    elif CONNECT_PHONE_BUTTON:
        builder.row(InlineKeyboardButton(text=CONNECT_PHONE, callback_data=f"connect_phone|{key_name}"))
        builder.row(
            InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{email}"),
            InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"),
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=CONNECT_DEVICE,
                callback_data=f"connect_device|{key_name}",
            )
        )

    builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    expiry_time_local = expiry_time.astimezone(moscow_tz)
    remaining_time = expiry_time_local - datetime.now(moscow_tz)
    days = remaining_time.days
    key_message_text = key_message_success(final_link, f"⏳ Осталось дней: {days} 📅")

    default_media_path = "img/pic.jpg"

    if target_message:
        await edit_or_send_message(
            target_message=target_message,
            text=key_message_text,
            reply_markup=builder.as_markup(),
            media_path=default_media_path,
        )
    else:
        photo = FSInputFile(default_media_path)
        await bot.send_photo(
            chat_id=tg_id,
            photo=photo,
            caption=key_message_text,
            reply_markup=builder.as_markup(),
        )

    if state:
        await state.clear()