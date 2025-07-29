import uuid
from datetime import datetime

import pytz
from aiogram import Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    Message,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import CONNECT_PHONE_BUTTON, SUPPORT_CHAT_URL
from database import (
    get_key_details,
    get_tariff_by_id,
    get_trial,
    update_balance,
    update_trial,
)
from handlers.buttons import (
    CONNECT_DEVICE,
    CONNECT_PHONE,
    MAIN_MENU,
    MY_SUB,
    PC_BUTTON,
    SUPPORT,
    TV_BUTTON,
)
from handlers.keys.key_utils import create_key_on_cluster
from handlers.texts import key_message_success
from handlers.utils import (
    edit_or_send_message,
    generate_random_email,
    get_least_loaded_cluster,
    is_full_remnawave_cluster,
)
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
    target_message = None
    safe_to_edit = False

    if isinstance(message_or_query, CallbackQuery) and message_or_query.message:
        target_message = message_or_query.message
        safe_to_edit = True
    elif isinstance(message_or_query, Message):
        target_message = message_or_query
        safe_to_edit = True

    while True:
        key_name = generate_random_email()
        existing_key = await get_key_details(session, key_name)
        if not existing_key:
            break

    client_id = str(uuid.uuid4())
    email = key_name.lower()
    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    try:
        data = await state.get_data() if state else {}
        is_trial = data.get("is_trial", False)

        device_limit = 0
        traffic_limit_gb = 0

        if plan:
            tariff = await get_tariff_by_id(session, plan)
            if tariff:
                if tariff.get("device_limit") is not None:
                    device_limit = int(tariff["device_limit"])
                if tariff.get("traffic_limit") is not None:
                    traffic_limit_gb = int(tariff["traffic_limit"])

        try:
            least_loaded_cluster = await get_least_loaded_cluster(session)
        except ValueError as e:
            logger.error(f"Нет доступных кластеров: {e}")
            error_message = str(e)

            if safe_to_edit:
                await edit_or_send_message(
                    target_message=target_message,
                    text=error_message,
                    reply_markup=None,
                )
            else:
                await bot.send_message(chat_id=tg_id, text=error_message)
            return

        await create_key_on_cluster(
            cluster_id=least_loaded_cluster,
            tg_id=tg_id,
            client_id=client_id,
            email=email,
            expiry_timestamp=expiry_timestamp,
            plan=plan,
            session=session,
            hwid_limit=device_limit,
            traffic_limit_bytes=traffic_limit_gb,
            is_trial=is_trial,
        )

        logger.info(f"[Key Creation] Ключ создан на кластере {least_loaded_cluster} для пользователя {tg_id}")

        key_record = await get_key_details(session, email)
        if not key_record:
            raise ValueError(f"Ключ не найден после создания: {email}")

        public_link = key_record.get("key")
        remnawave_link = key_record.get("remnawave_link")
        final_link = public_link or remnawave_link or ""

        if is_trial:
            trial_status = await get_trial(session, tg_id)
            if trial_status in [0, -1]:
                await update_trial(session, tg_id, 1)

        if data.get("tariff_id"):
            tariff = await get_tariff_by_id(session, data["tariff_id"])
            if tariff:
                await update_balance(session, tg_id, -tariff["price_rub"])
                logger.info(f"[Database] Баланс обновлён для пользователя {tg_id}")

    except Exception as e:
        logger.error(f"[Error] Ошибка при создании ключа для пользователя {tg_id}: {e}")
        error_message = "❌ Произошла ошибка при создании подписки. Пожалуйста, попробуйте снова."

        if safe_to_edit:
            await edit_or_send_message(
                target_message=target_message,
                text=error_message,
                reply_markup=None,
            )
        else:
            await bot.send_message(chat_id=tg_id, text=error_message)
        return

    builder = InlineKeyboardBuilder()
    if await is_full_remnawave_cluster(least_loaded_cluster, session):
        builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=final_link)))
        builder.row(InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"))
    elif CONNECT_PHONE_BUTTON:
        builder.row(InlineKeyboardButton(text=CONNECT_PHONE, callback_data=f"connect_phone|{key_name}"))
        builder.row(
            InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{email}"),
            InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"),
        )
    else:
        builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"))
    builder.row(InlineKeyboardButton(text=MY_SUB, callback_data=f"view_key|{key_name}"))
    builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    expiry_time_local = expiry_time.astimezone(moscow_tz)
    expiry_time_local - datetime.now(moscow_tz)

    tariff_info = None
    if plan:
        tariff_info = await get_tariff_by_id(session, plan)

    tariff_duration = tariff_info["name"]
    subgroup_title = tariff_info.get("subgroup_title", "") if tariff_info else ""

    key_message_text = key_message_success(
        final_link,
        tariff_name=tariff_duration,
        traffic_limit=tariff_info.get("traffic_limit", 0) if tariff_info else 0,
        device_limit=tariff_info.get("device_limit", 0) if tariff_info else 0,
        subgroup_title=subgroup_title,
    )

    default_media_path = "img/pic.jpg"
    if safe_to_edit:
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
