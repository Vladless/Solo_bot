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
from handlers.buttons import (
    ADD_SUB,
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
    RENEW,
    RENEW_FULL,
    TV_BUTTON,
    UNFREEZE,
)
from handlers.texts import FROZEN_SUBSCRIPTION_MSG, NO_SUBSCRIPTIONS_MSG, key_message
from handlers.utils import (
    edit_or_send_message,
    format_days,
    format_hours,
    format_minutes,
    format_months,
    get_russian_month,
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
        inline_keyboard, response_message = build_keys_response(records)
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


def build_keys_response(records):
    """
    Формирует сообщение и клавиатуру для устройств с указанием срока действия подписки.
    """
    builder = InlineKeyboardBuilder()
    moscow_tz = pytz.timezone("Europe/Moscow")

    if records:
        response_message = "<b>🔑 Список ваших подписок:</b>\n\n<blockquote>"
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
                formatted_date_full = expiry_date_full.strftime("до %d.%m.%y, %H:%M")
            else:
                formatted_date_full = "без срока действия"

            key_button = InlineKeyboardButton(
                text=f"🔑 {key_display}", callback_data=f"view_key|{email}"
            )
            rename_button = InlineKeyboardButton(
                text=ALIAS, callback_data=f"rename_key|{client_id}"
            )
            builder.row(key_button, rename_button)

            response_message += f"• <b>{key_display}</b> ({formatted_date_full})\n"

        response_message += (
            "</blockquote>\n\n<i>Нажмите на ✏️, чтобы переименовать подписку.</i>"
        )
    else:
        response_message = NO_SUBSCRIPTIONS_MSG

    builder.row(InlineKeyboardButton(text=ADD_SUB, callback_data="create_key"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    inline_keyboard = builder.as_markup()
    return inline_keyboard, response_message


@router.callback_query(F.data.startswith("rename_key|"))
async def handle_rename_key(callback: CallbackQuery, state: FSMContext):
    client_id = callback.data.split("|")[1]
    await state.set_state(RenameKeyState.waiting_for_new_alias)
    await state.update_data(client_id=client_id, target_message=callback.message)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data="cancel_and_back_to_view_keys")
    )

    await edit_or_send_message(
        target_message=callback.message,
        text="✏️ Введите новое имя подписки (до 10 символов):",
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
    alias = message.text.strip()

    if len(alias) > 10:
        await message.answer(
            "❌ Имя слишком длинное. Введите до 10 символов.\nПовторите ввод."
        )
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
            update(Key)
            .where(Key.tg_id == message.chat.id, Key.client_id == client_id)
            .values(alias=alias)
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


async def render_key_info(
    message: Message, session: Any, key_name: str, image_path: str
):
    from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD
    from panels.remnawave import RemnawaveAPI

    record = await get_key_details(session, key_name)
    if not record:
        await message.answer("<b>Информация о подписке не найдена.</b>")
        return

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
                text=UNFREEZE, callback_data=f"unfreeze_subscription|{key_name}"
            )
        )
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
    time_left = expiry_date - datetime.utcnow()

    if time_left.total_seconds() <= 0:
        days_left_message = "<b>🕒 Статус подписки:</b>\n🔴 Истекла"
    else:
        total_seconds = int(time_left.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60
        days_left_message = f"⏳ Осталось: <b>{format_days(days)}</b>, <b>{format_hours(hours)}</b>, <b>{format_minutes(minutes)}</b>"

    formatted_expiry_date = f"{expiry_date.strftime('%d')} {get_russian_month(expiry_date)} {expiry_date.strftime('%Y')} года"

    hwid_count = 0
    is_full_remnawave = await is_full_remnawave_cluster(server_name, session)
    if is_full_remnawave and client_id:
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

    tariff_name = ""
    traffic_limit = 0
    device_limit = 0
    tariff = None
    if record.get("tariff_id"):
        tariff = await get_tariff_by_id(session, record["tariff_id"])
        if tariff:
            tariff_name = tariff["name"]
            traffic_limit = tariff.get("traffic_limit", 0)
            device_limit = tariff.get("device_limit", 0)

    tariff_duration = ""
    if tariff and tariff.get("duration_days", 0) > 0:
        duration_days = tariff["duration_days"]
        if duration_days >= 30:
            months = duration_days // 30
            tariff_duration = format_months(months)
        else:
            tariff_duration = format_days(duration_days)

    response_message = key_message(
        final_link,
        formatted_expiry_date,
        days_left_message,
        server_name,
        server_name if USE_COUNTRY_SELECTION else None,
        hwid_count=hwid_count if device_limit is not None else 0,
        tariff_name=tariff_duration,
        traffic_limit=traffic_limit,
        device_limit=device_limit
    )

    if ENABLE_UPDATE_SUBSCRIPTION_BUTTON:
        builder.row(
            InlineKeyboardButton(
                text="🔄 Обновить подписку",
                callback_data=f"update_subscription|{key_name}",
            )
        )

    if is_full_remnawave and final_link:
        builder.row(
            InlineKeyboardButton(
                text=CONNECT_DEVICE, web_app=WebAppInfo(url=final_link)
            )
        )
        builder.row(
            InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{key_name}")
        )
    else:
        if CONNECT_PHONE_BUTTON:
            builder.row(
                InlineKeyboardButton(
                    text=CONNECT_PHONE, callback_data=f"connect_phone|{key_name}"
                )
            )
            builder.row(
                InlineKeyboardButton(
                    text=PC_BUTTON, callback_data=f"connect_pc|{key_name}"
                ),
                InlineKeyboardButton(
                    text=TV_BUTTON, callback_data=f"connect_tv|{key_name}"
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=CONNECT_DEVICE, callback_data=f"connect_device|{key_name}"
                )
            )

    if HWID_RESET_BUTTON and hwid_count > 0:
        builder.row(
            InlineKeyboardButton(
                text=HWID_BUTTON,
                callback_data=f"reset_hwid|{key_name}",
            )
        )

    if QRCODE:
        builder.row(InlineKeyboardButton(text=QR, callback_data=f"show_qr|{key_name}"))

    if ENABLE_DELETE_KEY_BUTTON:
        builder.row(
            InlineKeyboardButton(text=RENEW, callback_data=f"renew_key|{key_name}"),
            InlineKeyboardButton(text=DELETE, callback_data=f"delete_key|{key_name}"),
        )
    else:
        builder.row(
            InlineKeyboardButton(text=RENEW_FULL, callback_data=f"renew_key|{key_name}")
        )

    if USE_COUNTRY_SELECTION:
        builder.row(
            InlineKeyboardButton(
                text=CHANGE_LOCATION, callback_data=f"change_location|{key_name}"
            )
        )

    if TOGGLE_CLIENT:
        builder.row(
            InlineKeyboardButton(
                text=FREEZE, callback_data=f"freeze_subscription|{key_name}"
            )
        )

    builder.row(InlineKeyboardButton(text=BACK, callback_data="view_keys"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

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
    record = await get_key_details(session, key_name)
    if not record:
        await callback_query.answer("❌ Ключ не найден.", show_alert=True)
        return

    client_id = record.get("client_id")
    if not client_id:
        await callback_query.answer(
            "❌ У ключа отсутствует client_id.", show_alert=True
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
        await callback_query.answer("❌ Remnawave-сервер не найден.", show_alert=True)
        return

    api = RemnawaveAPI(remna_server["api_url"])
    if not await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
        await callback_query.answer(
            "❌ Авторизация в Remnawave не удалась.", show_alert=True
        )
        return

    devices = await api.get_user_hwid_devices(client_id)
    if not devices:
        await callback_query.answer("✅ Устройства не были привязаны.", show_alert=True)
    else:
        deleted = 0
        for device in devices:
            if await api.delete_user_hwid_device(client_id, device["hwid"]):
                deleted += 1
        await callback_query.answer(
            f"✅ Устройства сброшены ({deleted})", show_alert=True
        )

    image_path = os.path.join("img", "pic_view.jpg")
    await render_key_info(callback_query.message, session, key_name, image_path)
