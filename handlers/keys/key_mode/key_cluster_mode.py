import uuid

from datetime import datetime

import pytz

from aiogram import Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import update

from bot import bot
from config import REMNAWAVE_WEBAPP, REMNAWAVE_WEBAPP_OPEN_IN_BROWSER, SUPPORT_CHAT_URL
from core.bootstrap import BUTTONS_CONFIG, MODES_CONFIG
from database import (
    get_key_details,
    get_trial,
    update_balance,
    update_trial,
)
from database.models import Key
from handlers.buttons import (
    CONNECT_DEVICE,
    MAIN_MENU,
    MY_SUB,
    ROUTER_BUTTON,
    SUPPORT,
    TV_BUTTON,
)
from handlers.keys.operations import create_key_on_cluster
from handlers.tariffs.tariff_display import (
    build_key_created_message,
    get_effective_limits_for_key,
    resolve_price_to_charge,
    resolve_vless_enabled,
)
from handlers.utils import (
    edit_or_send_message,
    generate_random_email,
    get_least_loaded_cluster,
    is_full_remnawave_cluster,
)
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import (
    process_cluster_override,
    process_intercept_key_creation_message,
    process_key_creation_complete,
    process_remnawave_webapp_override,
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
    plan: int | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
    selected_price_rub: int | None = None,
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
        key_name = await generate_random_email(session=session)
        existing_key = await get_key_details(session, key_name)
        if not existing_key:
            break

    client_id = str(uuid.uuid4())
    email = key_name.lower()
    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    try:
        data = await state.get_data() if state else {}
        is_trial = data.get("is_trial", False)

        if selected_device_limit is None:
            selected_device_limit = data.get("config_selected_device_limit") or data.get("selected_device_limit")

        if selected_traffic_gb is None:
            selected_traffic_gb = data.get("config_selected_traffic_gb") or data.get("selected_traffic_limit_gb")

        effective_tariff_id = plan or data.get("tariff_id")

        device_limit, traffic_limit_bytes = await get_effective_limits_for_key(
            session=session,
            tariff_id=effective_tariff_id,
            selected_device_limit=selected_device_limit,
            selected_traffic_gb=selected_traffic_gb,
        )

        forced_cluster = await process_cluster_override(
            tg_id=tg_id,
            state_data=data,
            session=session,
            plan=plan,
        )

        if forced_cluster:
            least_loaded_cluster = forced_cluster
        else:
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

        if device_limit is None:
            device_limit = 0
        if traffic_limit_bytes is None:
            traffic_limit_bytes = 0

        if selected_price_rub is not None:
            price_to_charge = selected_price_rub
        else:
            price_to_charge = await resolve_price_to_charge(session, data)

        await create_key_on_cluster(
            cluster_id=least_loaded_cluster,
            tg_id=tg_id,
            client_id=client_id,
            email=email,
            expiry_timestamp=expiry_timestamp,
            plan=plan,
            session=session,
            hwid_limit=device_limit,
            traffic_limit_bytes=traffic_limit_bytes,
            is_trial=is_trial,
        )

        logger.info(f"[Key Creation] Ключ создан на кластере {least_loaded_cluster} для пользователя {tg_id}")

        await session.execute(
            update(Key)
            .where(Key.tg_id == tg_id, Key.email == email)
            .values(
                selected_device_limit=selected_device_limit,
                selected_traffic_limit=selected_traffic_gb,
                selected_price_rub=price_to_charge,
            )
        )
        await session.commit()

        key_record = await get_key_details(session, email)
        if not key_record:
            raise ValueError(f"Ключ не найден после создания: {email}")

        final_link = key_record.get("link", "")

        if is_trial:
            trial_status = await get_trial(session, tg_id)
            if trial_status in [0, -1]:
                await update_trial(session, tg_id, 1)

        if price_to_charge:
            await update_balance(session, tg_id, -int(price_to_charge))

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

    vless_enabled = False
    try:
        if plan:
            vless_enabled = await resolve_vless_enabled(session, plan)
        elif key_record.get("tariff_id"):
            vless_enabled = await resolve_vless_enabled(session, key_record["tariff_id"])
    except Exception:
        vless_enabled = False

    tv_button_enabled = bool(BUTTONS_CONFIG.get("ANDROID_TV_BUTTON_ENABLE"))

    builder = InlineKeyboardBuilder()
    if vless_enabled:
        builder.row(InlineKeyboardButton(text=ROUTER_BUTTON, callback_data=f"connect_router|{key_name}"))
    else:
        if await is_full_remnawave_cluster(least_loaded_cluster, session):
            use_webapp = bool(MODES_CONFIG.get("REMNAWAVE_WEBAPP_ENABLED", REMNAWAVE_WEBAPP))
            open_in_browser = bool(
                MODES_CONFIG.get("REMNAWAVE_WEBAPP_OPEN_IN_BROWSER", REMNAWAVE_WEBAPP_OPEN_IN_BROWSER)
            )
            if use_webapp and final_link:
                use_webapp = await process_remnawave_webapp_override(
                    remnawave_webapp=use_webapp,
                    final_link=final_link,
                    session=session,
                )

            if (
                use_webapp
                and final_link
                and isinstance(final_link, str)
                and final_link.startswith(("http://", "https://"))
            ):
                if open_in_browser:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, url=final_link))
                else:
                    builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=final_link)))
                if tv_button_enabled:
                    builder.row(InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{email}"))
            else:
                builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"))
        else:
            builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"))

    builder.row(InlineKeyboardButton(text=MY_SUB, callback_data=f"view_key|{key_name}"))
    builder.row(InlineKeyboardButton(text=SUPPORT, url=SUPPORT_CHAT_URL))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if await process_intercept_key_creation_message(
        chat_id=tg_id,
        session=session,
        target_message=message_or_query,
    ):
        return

    hook_commands = await process_key_creation_complete(
        chat_id=tg_id,
        admin=False,
        session=session,
        email=email,
        key_name=key_name,
    )
    if hook_commands:
        builder = insert_hook_buttons(builder, hook_commands)

    key_message_text = await build_key_created_message(
        session=session,
        key_record=key_record,
        final_link=final_link,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
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
        await bot.send_message(
            chat_id=tg_id,
            text=key_message_text,
            reply_markup=builder.as_markup(),
        )

    if state:
        await state.clear()
