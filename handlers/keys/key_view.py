import html
import os
import re
from datetime import datetime
from typing import Any

import pytz
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    CONNECT_PHONE_BUTTON,
    ENABLE_DELETE_KEY_BUTTON,
    ENABLE_UPDATE_SUBSCRIPTION_BUTTON,
    HWID_RESET_BUTTON,
    QRCODE,
    TOGGLE_CLIENT,
    USE_COUNTRY_SELECTION,
)
from database import get_key_details, get_keys, get_servers, get_tariff_by_id
from database.models import Key
from handlers.localization import get_user_texts, get_user_buttons, get_localized_month_for_user
from handlers.utils import (
    edit_or_send_message,
    format_days,
    format_hours,
    format_minutes,
    format_months,
    is_full_remnawave_cluster,
)
from logger import logger

router = Router()


class RenameKeyState(StatesGroup):
    waiting_for_new_alias = State()


@router.callback_query(F.data == "view_keys")
@router.message(F.text == "/subs")
async def process_callback_or_message_view_keys(
    callback_query_or_message: Message | CallbackQuery, session: Any
):
    if isinstance(callback_query_or_message, CallbackQuery):
        target_message = callback_query_or_message.message
    else:
        target_message = callback_query_or_message

    tg_id = callback_query_or_message.from_user.id

    try:
        records = await get_keys(session, tg_id)
        texts = await get_user_texts(session, tg_id)
        buttons = await get_user_buttons(session, tg_id)
        inline_keyboard, response_message = build_keys_response(records, texts, buttons)
        image_path = os.path.join("img", "pic_keys.jpg")

        await edit_or_send_message(
            target_message=target_message,
            text=response_message,
            reply_markup=inline_keyboard,
            media_path=image_path,
        )
    except Exception as e:
        error_message = texts.ERROR_GETTING_KEYS.format(error=e)
        await target_message.answer(text=error_message)


def build_keys_response(records, texts, buttons):
    """
    Формирует сообщение и клавиатуру для устройств с указанием срока действия подписки.
    """
    builder = InlineKeyboardBuilder()
    moscow_tz = pytz.timezone("Europe/Moscow")

    if records:
        response_message = texts.SUBSCRIPTION_LIST_HEADER
        for record in records:
            alias = record.alias
            email = record.email
            client_id = record.client_id
            expiry_time = record.expiry_time

            key_display = html.escape(alias.strip() if alias else email)

            if expiry_time:
                expiry_date_full = datetime.fromtimestamp(
                    expiry_time / 1000, tz=moscow_tz
                )
                formatted_date_full = expiry_date_full.strftime(texts.EXPIRY_DATE_FORMAT)
            else:
                formatted_date_full = texts.SUBSCRIPTION_NO_EXPIRY

            key_button = InlineKeyboardButton(
                text=f"🔑 {key_display}", callback_data=f"view_key|{email}"
            )
            rename_button = InlineKeyboardButton(
                text=buttons.ALIAS, callback_data=f"rename_key|{client_id}"
            )
            builder.row(key_button, rename_button)

            response_message += f"• <b>{key_display}</b> ({formatted_date_full})\n"

        response_message += texts.SUBSCRIPTION_RENAME_HINT
    else:
        response_message = texts.NO_SUBSCRIPTIONS_MSG

    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    inline_keyboard = builder.as_markup()
    return inline_keyboard, response_message


@router.callback_query(F.data.startswith("rename_key|"))
async def handle_rename_key(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    client_id = callback.data.split("|")[1]
    tg_id = callback.from_user.id
    await state.set_state(RenameKeyState.waiting_for_new_alias)
    await state.update_data(client_id=client_id, target_message=callback.message)

    texts = await get_user_texts(session, tg_id)
    buttons = await get_user_buttons(session, tg_id)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=buttons.BACK, callback_data="cancel_and_back_to_view_keys")
    )

    await edit_or_send_message(
        target_message=callback.message,
        text=texts.ENTER_NEW_SUBSCRIPTION_NAME,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "cancel_and_back_to_view_keys")
async def cancel_and_back(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
):
    await state.clear()
    await callback.answer()
    await process_callback_or_message_view_keys(callback, session)


@router.message(F.text, RenameKeyState.waiting_for_new_alias)
async def handle_new_alias_input(
    message: Message, state: FSMContext, session: AsyncSession
):
    tg_id = message.from_user.id
    texts = await get_user_texts(session, tg_id)
    alias = message.text.strip()

    if len(alias) > 10:
        await message.answer(texts.SUBSCRIPTION_NAME_TOO_LONG)
        return

    if not alias or not re.match(r"^[a-zA-Zа-яА-ЯёЁ0-9@._-]+$", alias):
        await message.answer(texts.SUBSCRIPTION_INVALID_CHARACTERS)
        return

    data = await state.get_data()
    client_id = data.get("client_id")

    try:
        await session.execute(
            update(Key)
            .where(Key.tg_id == message.chat.id, Key.client_id == client_id)
            .values(alias=alias)
        )
        await session.commit()

    except Exception as e:
        await message.answer(texts.SUBSCRIPTION_RENAME_ERROR)
        logger.error(texts.ERROR_UPDATING_ALIAS.format(error=e))
    finally:
        await state.clear()

    await process_callback_or_message_view_keys(message, session)


@router.callback_query(F.data.startswith("view_key|"))
async def process_callback_view_key(callback_query: CallbackQuery, session: Any):
    key_name = callback_query.data.split("|")[1]
    tg_id = callback_query.from_user.id
    image_path = os.path.join("img", "pic_view.jpg")
    await render_key_info(callback_query.message, session, key_name, image_path, tg_id)


async def render_key_info(
    message: Message, session: Any, key_name: str, image_path: str, tg_id: int
):
    from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
    from panels.remnawave import RemnawaveAPI

    record = await get_key_details(session, key_name)
    if not record:
        await message.answer(texts.SUBSCRIPTION_INFO_NOT_FOUND)
        return

    texts = await get_user_texts(session, tg_id)
    buttons = await get_user_buttons(session, tg_id)

    is_frozen = record["is_frozen"]
    record["email"]
    client_id = record.get("client_id")
    remnawave_link = record.get("remnawave_link")
    key = record.get("key")
    final_link = key or remnawave_link

    builder = InlineKeyboardBuilder()

    if is_frozen:
        builder.row(
            InlineKeyboardButton(
                text=buttons.UNFREEZE, callback_data=f"unfreeze_subscription|{key_name}"
            )
        )
        builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data="view_keys"))
        builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=message,
            text=texts.FROZEN_SUBSCRIPTION_MSG,
            reply_markup=builder.as_markup(),
            media_path=image_path,
        )
        return

    expiry_time = record["expiry_time"]
    server_name = record["server_id"]
    expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
    time_left = expiry_date - datetime.utcnow()

    if time_left.total_seconds() <= 0:
        days_left_message = texts.SUBSCRIPTION_STATUS_EXPIRED
    else:
        total_seconds = int(time_left.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        days_left_message = texts.SUBSCRIPTION_TIME_LEFT.format(
            days=format_days(days), 
            hours=format_hours(hours), 
            minutes=format_minutes(minutes)
        )

    formatted_expiry_date = texts.SUBSCRIPTION_EXPIRY_DATE_FORMAT.format(
        day=expiry_date.strftime('%d'),
        month=await get_localized_month_for_user(session, tg_id, expiry_date),
        year=expiry_date.strftime('%Y')
    )

    hwid_count = 0
    is_full_remnawave = await is_full_remnawave_cluster(server_name, session)
    if is_full_remnawave and client_id:
        try:
            servers = await get_servers(session)
            remna_server = next(
                (
                    srv
                    for cl in servers.values()
                    for srv in cl
                    if srv.get("panel_type") == "remnawave"
                ),
                None,
            )
            if remna_server:
                api = RemnawaveAPI(remna_server["api_url"])
                if await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    devices = await api.get_user_hwid_devices(client_id)
                    hwid_count = len(devices or [])
        except Exception as e:
            logger.error(texts.ERROR_GETTING_HWID.format(client_id=client_id, error=e))

    tariff_name = ""
    traffic_limit = 0
    device_limit = 0
    subgroup_title = ""
    tariff = None
    if record.get("tariff_id"):
        tariff = await get_tariff_by_id(session, record["tariff_id"])
        if tariff:
            tariff_name = tariff["name"]
            traffic_limit = tariff.get("traffic_limit", 0)
            device_limit = tariff.get("device_limit", 0)
            subgroup_title = tariff.get("subgroup_title", "")

    tariff_duration = tariff_name

    response_message = texts.key_message(
        final_link,
        formatted_expiry_date,
        days_left_message,
        server_name,
        server_name if USE_COUNTRY_SELECTION else None,
        hwid_count=hwid_count if device_limit is not None else 0,
        tariff_name=tariff_duration,
        traffic_limit=traffic_limit,
        device_limit=device_limit,
        subgroup_title=subgroup_title
    )

    if ENABLE_UPDATE_SUBSCRIPTION_BUTTON:
        builder.row(
            InlineKeyboardButton(
                text=texts.UPDATE_SUBSCRIPTION_BUTTON,
                callback_data=f"update_subscription|{key_name}",
            )
        )

    if is_full_remnawave and final_link:
        builder.row(
            InlineKeyboardButton(
                text=buttons.CONNECT_DEVICE, web_app=WebAppInfo(url=final_link)
            )
        )
        builder.row(
            InlineKeyboardButton(text=buttons.TV_BUTTON, callback_data=f"connect_tv|{key_name}")
        )
    else:
        if CONNECT_PHONE_BUTTON:
            builder.row(
                InlineKeyboardButton(
                    text=buttons.CONNECT_PHONE, callback_data=f"connect_phone|{key_name}"
                )
            )
            builder.row(
                InlineKeyboardButton(
                    text=buttons.PC_BUTTON, callback_data=f"connect_pc|{key_name}"
                ),
                InlineKeyboardButton(
                    text=buttons.TV_BUTTON, callback_data=f"connect_tv|{key_name}"
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=buttons.CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"
                )
            )

    if HWID_RESET_BUTTON and hwid_count > 0:
        builder.row(
            InlineKeyboardButton(
                text=buttons.HWID_BUTTON,
                callback_data=f"reset_hwid|{key_name}",
            )
        )

    if QRCODE:
        builder.row(InlineKeyboardButton(text=buttons.QR, callback_data=f"show_qr|{key_name}"))

    if ENABLE_DELETE_KEY_BUTTON:
        builder.row(
            InlineKeyboardButton(text=buttons.DELETE, callback_data=f"delete_key|{key_name}"),
        )

    if USE_COUNTRY_SELECTION:
        builder.row(
            InlineKeyboardButton(
                text=buttons.CHANGE_LOCATION, callback_data=f"change_location|{key_name}"
            )
        )

    if TOGGLE_CLIENT:
        builder.row(
            InlineKeyboardButton(
                text=buttons.FREEZE, callback_data=f"freeze_subscription|{key_name}"
            )
        )

    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data="view_keys"))
    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=message,
        text=response_message,
        reply_markup=builder.as_markup(),
        media_path=image_path,
    )


@router.callback_query(F.data.startswith("reset_hwid|"))
async def handle_reset_hwid(callback_query: CallbackQuery, session: Any):
    from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
    from panels.remnawave import RemnawaveAPI

    key_name = callback_query.data.split("|")[1]
    tg_id = callback_query.from_user.id
    texts = await get_user_texts(session, tg_id)
    
    record = await get_key_details(session, key_name)
    if not record:
        await callback_query.answer(texts.KEY_NOT_FOUND_ALERT, show_alert=True)
        return

    client_id = record.get("client_id")
    if not client_id:
        await callback_query.answer(
            texts.KEY_NO_CLIENT_ID_ALERT, show_alert=True
        )
        return

    servers = await get_servers(session=session)
    remna_server = next(
        (
            srv
            for cl in servers.values()
            for srv in cl
            if srv.get("panel_type") == "remnawave"
        ),
        None,
    )
    if not remna_server:
        await callback_query.answer(texts.REMNAWAVE_SERVER_NOT_FOUND, show_alert=True)
        return

    api = RemnawaveAPI(remna_server["api_url"])
    if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
        await callback_query.answer(
            texts.REMNAWAVE_AUTH_FAILED, show_alert=True
        )
        return

    devices = await api.get_user_hwid_devices(client_id)
    if not devices:
        await callback_query.answer(texts.DEVICES_NOT_BOUND, show_alert=True)
    else:
        deleted = 0
        for device in devices:
            if await api.delete_user_hwid_device(client_id, device["hwid"]):
                deleted += 1
        await callback_query.answer(
            texts.DEVICES_RESET_SUCCESS.format(deleted=deleted), show_alert=True
        )

    image_path = os.path.join("img", "pic_view.jpg")
    tg_id = callback_query.from_user.id
    await render_key_info(callback_query.message, session, key_name, image_path, tg_id)


@router.callback_query(F.data == "renew_menu")
@router.message(F.text == "/extend")
async def process_renew_menu(callback_query_or_message: CallbackQuery | Message, session: Any):
    try:
        if isinstance(callback_query_or_message, CallbackQuery):
            target_message = callback_query_or_message.message
            tg_id = callback_query_or_message.from_user.id
        else:
            target_message = callback_query_or_message
            tg_id = callback_query_or_message.from_user.id
            
        texts = await get_user_texts(session, tg_id)
        buttons = await get_user_buttons(session, tg_id)
        records = await get_keys(session, tg_id)
        servers_dict = await get_servers(session)
        all_server_names = set()
        for servers in servers_dict.values():
            for s in servers:
                all_server_names.add(s["server_name"])
        builder = InlineKeyboardBuilder()
        moscow_tz = pytz.timezone("Europe/Moscow")
        if records:
            for record in records:
                if getattr(record, 'is_frozen', False):
                    continue
                alias = record.alias
                email = record.email
                expiry_time = record.expiry_time
                server_id = record.server_id
                key_display = alias.strip() if alias else email

                if expiry_time:
                    expiry_date_full = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz)
                    now = datetime.now(moscow_tz)
                    days_left = (expiry_date_full - now).days
                    if (expiry_date_full - now).total_seconds() <= 0:
                        days_text = texts.SUBSCRIPTION_EXPIRED_SHORT
                    else:
                        days_text = format_days(days_left)
                else:
                    days_text = texts.SUBSCRIPTION_EXPIRED
                server_info = f" ({server_id})" if server_id in all_server_names else ""
                btn_text = f"🔑 {key_display} (⏳{days_text}) {server_info}"
                builder.row(InlineKeyboardButton(text=btn_text, callback_data=f"renew_key|{email}"))
        text = texts.SELECT_SUBSCRIPTION_FOR_RENEWAL_OR_BUY
        builder.row(InlineKeyboardButton(text=buttons.ADD_SUB, callback_data="create_key"))
        builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))
        image_path = os.path.join("img", "pic_view.jpg")
        await edit_or_send_message(
            target_message=target_message,
            text=text,
            reply_markup=builder.as_markup(),
            media_path=image_path,
        )
    except Exception as e:
        error_message = texts.ERROR_GETTING_SUBSCRIPTIONS_FOR_RENEWAL.format(error=e)
        await target_message.answer(text=error_message)
