import asyncio
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
    HAPP_CRYPTOLINK,
    HWID_RESET_BUTTON,
    QRCODE,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    REMNAWAVE_WEBAPP,
    TOGGLE_CLIENT,
    USE_COUNTRY_SELECTION,
)
from database import get_key_details, get_keys, get_servers, get_tariff_by_id
from database.models import Key
from handlers.buttons import (
    ALIAS,
    BACK,
    CHANGE_LOCATION,
    CONNECT_DEVICE,
    CONNECT_PHONE,
    DELETE,
    FREEZE,
    HWID_BUTTON,
    MAIN_MENU,
    PC_BUTTON,
    QR,
    RENEW_KEY,
    ROUTER_BUTTON,
    TV_BUTTON,
    UNFREEZE,
)
from handlers.texts import (
    DAYS_LEFT_MESSAGE,
    FROZEN_SUBSCRIPTION_MSG,
    KEYS_FOOTER,
    KEYS_HEADER,
    NO_SUBSCRIPTIONS_MSG,
    RENAME_KEY_PROMPT,
    key_message,
)
from handlers.utils import (
    edit_or_send_message,
    format_days,
    format_hours,
    format_minutes,
    get_russian_month,
    is_full_remnawave_cluster,
)
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from logger import logger
from panels.remnawave import RemnawaveAPI


router = Router()


class RenameKeyState(StatesGroup):
    waiting_for_new_alias = State()


@router.callback_query(F.data == "view_keys")
@router.message(F.text == "/subs")
async def process_callback_or_message_view_keys(callback_query_or_message: Message | CallbackQuery, session: Any):
    if isinstance(callback_query_or_message, CallbackQuery):
        target_message = callback_query_or_message.message
    else:
        target_message = callback_query_or_message

    tg_id = callback_query_or_message.from_user.id

    try:
        records = await get_keys(session, tg_id)

        if records and len(records) == 1:
            key_name = records[0].email
            image_path = os.path.join("img", "pic_view.jpg")
            await render_key_info(target_message, session, key_name, image_path)
            return

        inline_keyboard, response_message = await build_keys_response(records, session)
        image_path = os.path.join("img", "pic_keys.jpg")

        await edit_or_send_message(
            target_message=target_message,
            text=response_message,
            reply_markup=inline_keyboard,
            media_path=image_path,
        )
    except Exception as e:
        error_message = f"Ошибка при получении ключей: {e}"
        await target_message.answer(text=error_message)


async def build_keys_response(records, session):
    """
    Формирует сообщение и клавиатуру для устройств с указанием срока действия подписки.
    """
    builder = InlineKeyboardBuilder()
    moscow_tz = pytz.timezone("Europe/Moscow")

    if records:
        response_message = KEYS_HEADER
        for record in records:
            alias = record.alias
            email = record.email
            client_id = record.client_id
            expiry_time = record.expiry_time

            key_display = html.escape(alias.strip() if alias else email)

            if expiry_time:
                expiry_date_full = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz)
                formatted_date_full = expiry_date_full.strftime("до %d.%m.%y, %H:%M")
            else:
                formatted_date_full = "без срока действия"

            is_vless = False
            if hasattr(record, "tariff_id") and record.tariff_id:
                try:
                    tariff = await get_tariff_by_id(session, record.tariff_id)
                    if tariff and tariff.get("vless"):
                        is_vless = True
                except:
                    pass

            icon = "📶" if is_vless else "🔑"

            key_button = InlineKeyboardButton(text=f"{icon} {key_display}", callback_data=f"view_key|{email}")
            rename_button = InlineKeyboardButton(text=ALIAS, callback_data=f"rename_key|{client_id}")
            builder.row(key_button, rename_button)

            response_message += f"• <b>{key_display}</b> ({formatted_date_full})\n"

        response_message += KEYS_FOOTER
    else:
        response_message = NO_SUBSCRIPTIONS_MSG

    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    inline_keyboard = builder.as_markup()
    return inline_keyboard, response_message


@router.callback_query(F.data.startswith("rename_key|"))
async def handle_rename_key(callback: CallbackQuery, state: FSMContext):
    client_id = callback.data.split("|")[1]
    await state.set_state(RenameKeyState.waiting_for_new_alias)
    await state.update_data(client_id=client_id, target_message=callback.message)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="cancel_and_back_to_view_keys"))

    await edit_or_send_message(
        target_message=callback.message,
        text=RENAME_KEY_PROMPT,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "cancel_and_back_to_view_keys")
async def cancel_and_back(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    await process_callback_or_message_view_keys(callback, session)


@router.message(F.text, RenameKeyState.waiting_for_new_alias)
async def handle_new_alias_input(message: Message, state: FSMContext, session: AsyncSession):
    alias = message.text.strip()

    if len(alias) > 10:
        await message.answer("❌ Имя слишком длинное. Введите до 10 символов.\nПовторите ввод.")
        return

    if not alias or not re.match(r"^[a-zA-Zа-яА-ЯёЁ0-9@._-]+$", alias):
        await message.answer(
            "❌ Введены недопустимые символы или имя пустое. Используйте только буквы, цифры и @._-\nПовторите ввод."
        )
        return

    data = await state.get_data()
    client_id = data.get("client_id")

    try:
        await session.execute(
            update(Key).where(Key.tg_id == message.chat.id, Key.client_id == client_id).values(alias=alias)
        )
        await session.commit()

    except Exception as e:
        await message.answer("❌ Не удалось переименовать подписку.")
        logger.error(f"Ошибка при обновлении alias: {e}")
    finally:
        await state.clear()

    await process_callback_or_message_view_keys(message, session)


@router.callback_query(F.data.startswith("view_key|"))
async def process_callback_view_key(callback_query: CallbackQuery, session: Any):
    key_name = callback_query.data.split("|")[1]
    image_path = os.path.join("img", "pic_view.jpg")
    await render_key_info(callback_query.message, session, key_name, image_path)


async def render_key_info(message: Message, session: Any, key_name: str, image_path: str):
    record = await get_key_details(session, key_name)
    if not record:
        await message.answer("<b>Информация о подписке не найдена.</b>")
        return

    is_frozen = record["is_frozen"]
    client_id = record.get("client_id")
    remnawave_link = record.get("remnawave_link")
    key = record.get("key")
    final_link = key or remnawave_link

    builder = InlineKeyboardBuilder()

    if is_frozen:
        builder.row(InlineKeyboardButton(text=UNFREEZE, callback_data=f"unfreeze_subscription|{key_name}"))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="view_keys"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=message,
            text=FROZEN_SUBSCRIPTION_MSG,
            reply_markup=builder.as_markup(),
            media_path=image_path,
        )
        return

    expiry_time = record["expiry_time"]
    server_name = record["server_id"]
    expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
    now = datetime.utcnow()
    time_left = expiry_date - now

    if time_left.total_seconds() <= 0:
        days_left_message = DAYS_LEFT_MESSAGE
    else:
        total_seconds = int(time_left.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        days_left_message = (
            f"⏳ Осталось: <b>{format_days(days)}</b>, <b>{format_hours(hours)}</b>, <b>{format_minutes(minutes)}</b>"
        )

    formatted_expiry_date = (
        f"{expiry_date.strftime('%d')} {get_russian_month(expiry_date)} {expiry_date.strftime('%Y')} года"
    )

    is_full_task = asyncio.create_task(is_full_remnawave_cluster(server_name, session))
    tariff_task = (
        asyncio.create_task(get_tariff_by_id(session, record["tariff_id"])) if record.get("tariff_id") else None
    )

    is_full_remnawave = await is_full_task
    tariff = await tariff_task if tariff_task else None

    hwid_count = 0
    remna_used_gb = None
    if is_full_remnawave and client_id:
        try:
            servers = await get_servers(session)
            remna_server = next(
                (srv for cl in servers.values() for srv in cl if srv.get("panel_type") == "remnawave"), None
            )
            if remna_server:
                api = RemnawaveAPI(remna_server["api_url"])
                if await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    devices = await api.get_user_hwid_devices(client_id)
                    hwid_count = len(devices or [])
                    user_data = await api.get_user_by_uuid(client_id)
                    if user_data:
                        used_bytes = user_data.get("usedTrafficBytes", 0)
                        remna_used_gb = round(used_bytes / 1073741824, 1)
        except Exception as e:
            logger.error(f"Ошибка при получении данных Remnawave для {client_id}: {e}")

    tariff_name = ""
    traffic_limit = 0
    device_limit = 0
    subgroup_title = ""
    vless_enabled = False
    if tariff:
        tariff_name = tariff["name"]
        traffic_limit = tariff.get("traffic_limit", 0)
        device_limit = tariff.get("device_limit", 0)
        subgroup_title = tariff.get("subgroup_title", "")
        vless_enabled = bool(tariff.get("vless"))

    tariff_duration = tariff_name

    response_message = key_message(
        final_link,
        formatted_expiry_date,
        days_left_message,
        server_name,
        server_name if USE_COUNTRY_SELECTION else None,
        hwid_count=hwid_count if device_limit is not None else 0,
        tariff_name=tariff_duration,
        traffic_limit=traffic_limit,
        device_limit=device_limit,
        subgroup_title=subgroup_title,
        is_remnawave=is_full_remnawave,
        remna_used_gb=remna_used_gb,
    )

    if is_full_remnawave and final_link and REMNAWAVE_WEBAPP and not HAPP_CRYPTOLINK:
        if vless_enabled:
            builder.row(InlineKeyboardButton(text=ROUTER_BUTTON, callback_data=f"connect_router|{key_name}"))
        else:
            builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, web_app=WebAppInfo(url=final_link)))
            builder.row(InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{key_name}"))
    else:
        if CONNECT_PHONE_BUTTON:
            builder.row(InlineKeyboardButton(text=CONNECT_PHONE, callback_data=f"connect_phone|{key_name}"))
            if vless_enabled:
                builder.row(InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{key_name}"))
                builder.row(InlineKeyboardButton(text=ROUTER_BUTTON, callback_data=f"connect_router|{key_name}"))
            else:
                builder.row(
                    InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{key_name}"),
                    InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{key_name}"),
                )
        else:
            if vless_enabled:
                builder.row(InlineKeyboardButton(text=ROUTER_BUTTON, callback_data=f"connect_router|{key_name}"))
            else:
                builder.row(InlineKeyboardButton(text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"))

    builder.row(InlineKeyboardButton(text=RENEW_KEY, callback_data=f"renew_key|{key_name}"))

    if HWID_RESET_BUTTON and hwid_count > 0:
        builder.row(InlineKeyboardButton(text=HWID_BUTTON, callback_data=f"reset_hwid|{key_name}"))

    if QRCODE:
        builder.row(InlineKeyboardButton(text=QR, callback_data=f"show_qr|{key_name}"))

    if ENABLE_DELETE_KEY_BUTTON:
        builder.row(InlineKeyboardButton(text=DELETE, callback_data=f"delete_key|{key_name}"))

    if USE_COUNTRY_SELECTION:
        builder.row(InlineKeyboardButton(text=CHANGE_LOCATION, callback_data=f"change_location|{key_name}"))

    if TOGGLE_CLIENT:
        builder.row(InlineKeyboardButton(text=FREEZE, callback_data=f"freeze_subscription|{key_name}"))

    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    module_buttons = await run_hooks("view_key_menu", key_name=key_name, session=session)
    builder = insert_hook_buttons(builder, module_buttons)

    await edit_or_send_message(
        target_message=message,
        text=response_message,
        reply_markup=builder.as_markup(),
        media_path=image_path,
    )


@router.callback_query(F.data.startswith("reset_hwid|"))
async def handle_reset_hwid(callback_query: CallbackQuery, session: Any):
    key_name = callback_query.data.split("|")[1]

    record_task = asyncio.create_task(get_key_details(session, key_name))
    servers_task = asyncio.create_task(get_servers(session=session))

    record = await record_task
    if not record:
        await callback_query.answer("❌ Ключ не найден.", show_alert=True)
        return

    client_id = record.get("client_id")
    if not client_id:
        await callback_query.answer("❌ У ключа отсутствует client_id.", show_alert=True)
        return

    servers = await servers_task
    remna_server = next((srv for cl in servers.values() for srv in cl if srv.get("panel_type") == "remnawave"), None)
    if not remna_server:
        await callback_query.answer("❌ Remnawave-сервер не найден.", show_alert=True)
        return

    api = RemnawaveAPI(remna_server["api_url"])
    if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
        await callback_query.answer("❌ Авторизация в Remnawave не удалась.", show_alert=True)
        return

    devices = await api.get_user_hwid_devices(client_id)
    if not devices:
        await callback_query.answer("✅ Устройства не были привязаны.", show_alert=True)
    else:
        deleted = 0
        for device in devices:
            if await api.delete_user_hwid_device(client_id, device["hwid"]):
                deleted += 1
        await callback_query.answer(f"✅ Устройства сброшены ({deleted})", show_alert=True)

    hook_result = await run_hooks(
        "after_hwid_reset", chat_id=callback_query.from_user.id, admin=False, session=session, key_name=key_name
    )
    if hook_result and any("redirect_to_profile" in str(result) for result in hook_result):
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        if callback_query.message.text:
            await callback_query.message.edit_text("✅ Устройства сброшены", reply_markup=kb.as_markup())
        else:
            await callback_query.message.edit_caption(caption="✅ Устройства сброшены", reply_markup=kb.as_markup())
        return

    image_path = os.path.join("img", "pic_view.jpg")
    await render_key_info(callback_query.message, session, key_name, image_path)
