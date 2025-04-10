from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
import pytz
import html
import os
import re
from datetime import datetime
from aiogram.fsm.state import State, StatesGroup
from typing import Any

from aiogram.fsm.context import FSMContext

from config import (
    CONNECT_PHONE_BUTTON,
    ENABLE_DELETE_KEY_BUTTON,
    ENABLE_UPDATE_SUBSCRIPTION_BUTTON,
    PUBLIC_LINK,
    QRCODE,
    TOGGLE_CLIENT,
    USE_COUNTRY_SELECTION,
)
from database import (
    get_key_details,
    get_keys,
)
from handlers.buttons import (
    ADD_SUB,
    ALIAS,
    BACK,
    CHANGE_LOCATION,
    CONNECT_DEVICE,
    CONNECT_PHONE,
    DELETE,
    FREEZE,
    MAIN_MENU,
    PC_BUTTON,
    QR,
    RENEW,
    RENEW_FULL,
    TV_BUTTON,
    UNFREEZE,
)
from handlers.texts import (
    FROZEN_SUBSCRIPTION_MSG,
    NO_SUBSCRIPTIONS_MSG,
    key_message,
)
from handlers.utils import edit_or_send_message, handle_error, is_full_remnawave_cluster
from logger import logger


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

    try:
        records = await get_keys(target_message.chat.id, session)
        inline_keyboard, response_message = build_keys_response(records)
        image_path = os.path.join("img", "pic_keys.jpg")

        await edit_or_send_message(
            target_message=target_message, text=response_message, reply_markup=inline_keyboard, media_path=image_path
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
            alias = record.get("alias")
            email = record["email"]
            client_id = record["client_id"]
            expiry_time = record.get("expiry_time")

            key_display = html.escape(alias.strip() if alias else email)

            if expiry_time:
                expiry_date_full = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz)
                formatted_date_full = expiry_date_full.strftime("до %d.%m.%y, %H:%M")
            else:
                formatted_date_full = "без срока действия"

            key_button = InlineKeyboardButton(text=f"🔑 {key_display}", callback_data=f"view_key|{email}")
            rename_button = InlineKeyboardButton(text=ALIAS, callback_data=f"rename_key|{client_id}")
            builder.row(key_button, rename_button)

            response_message += f"• <b>{key_display}</b> ({formatted_date_full})\n"

        response_message += "</blockquote>\n\n<i>Нажмите на ✏️, чтобы переименовать подписку.</i>"
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
    builder.row(InlineKeyboardButton(text=BACK, callback_data="view_keys"))

    await edit_or_send_message(
        target_message=callback.message,
        text="✏️ Введите новое имя подписки (до 10 символов):",
        reply_markup=builder.as_markup()
    )


@router.message(F.text, RenameKeyState.waiting_for_new_alias)
async def handle_new_alias_input(message: Message, state: FSMContext, session: Any):
    alias = message.text.strip()

    if len(alias) > 10:
        await message.answer("❌ Имя слишком длинное. Введите до 10 символов.\nПовторите ввод.")
        return

    if not alias or not re.match(r"^[a-zA-Zа-яА-ЯёЁ0-9@._-]+$", alias):
        await message.answer("❌ Введены недопустимые символы или имя пустое. Используйте только буквы, цифры и @._-\nПовторите ввод.")
        return

    data = await state.get_data()
    client_id = data.get("client_id")

    try:
        await session.execute(
            "UPDATE keys SET alias = $1 WHERE tg_id = $2 AND client_id = $3",
            alias,
            message.chat.id,
            client_id,
        )
    except Exception as e:
        await message.answer("❌ Не удалось переименовать подписку.")
        logger.error(f"Ошибка при обновлении alias: {e}")
    finally:
        await state.clear()

    await process_callback_or_message_view_keys(message, session)


@router.callback_query(F.data.startswith("view_key|"))
async def process_callback_view_key(callback_query: CallbackQuery, session: Any):
    tg_id = callback_query.message.chat.id
    key_name = callback_query.data.split("|")[1]
    try:
        record = await get_key_details(key_name, session)
        if not record:
            await callback_query.message.answer(text="<b>Информация о подписке не найдена.</b>")
            return

        is_frozen = record["is_frozen"]

        builder = InlineKeyboardBuilder()
        image_path = os.path.join("img", "pic_view.jpg")
        if not os.path.isfile(image_path):
            await callback_query.message.answer("Файл изображения не найден.")
            return

        if is_frozen:
            builder.row(
                InlineKeyboardButton(
                    text=UNFREEZE,
                    callback_data=f"unfreeze_subscription|{key_name}",
                )
            )
            builder.row(InlineKeyboardButton(text=BACK, callback_data="view_keys"))
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

            await edit_or_send_message(
                target_message=callback_query.message,
                text=FROZEN_SUBSCRIPTION_MSG,
                reply_markup=builder.as_markup(),
                media_path=image_path,
            )
            return

        key = record.get("key")
        remnawave_link = record.get("remnawave_link")
        final_link = key or remnawave_link

        expiry_time = record["expiry_time"]
        server_name = record["server_id"]
        expiry_date = datetime.utcfromtimestamp(expiry_time / 1000)
        time_left = expiry_date - datetime.utcnow()

        if time_left.total_seconds() <= 0:
            days_left_message = "<b>🕒 Статус подписки:</b>\n🔴 Истекла\nОсталось часов: 0\nОсталось минут: 0"
        else:
            total_seconds = int(time_left.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            days_left_message = f"Осталось: <b>{days}</b> дней, <b>{hours}</b> часов, <b>{minutes}</b> минут"

        formatted_expiry_date = expiry_date.strftime("%d %B %Y года")
        response_message = key_message(
            final_link,
            formatted_expiry_date,
            days_left_message,
            server_name,
            server_name if USE_COUNTRY_SELECTION else None,
        )

        if (not key or not key.startswith(PUBLIC_LINK)) or ENABLE_UPDATE_SUBSCRIPTION_BUTTON:
            builder.row(
                InlineKeyboardButton(
                    text="🔄 Обновить подписку",
                    callback_data=f"update_subscription|{key_name}",
                )
            )

        is_full_remnawave = await is_full_remnawave_cluster(server_name, session)
        if is_full_remnawave and final_link:
            builder.row(
                InlineKeyboardButton(
                    text=CONNECT_DEVICE,
                    web_app=WebAppInfo(url=final_link),
                )
            )
        else:
            if CONNECT_PHONE_BUTTON:
                builder.row(
                    InlineKeyboardButton(
                        text=CONNECT_PHONE,
                        callback_data=f"connect_phone|{key_name}",
                    )
                )
                builder.row(
                    InlineKeyboardButton(text=PC_BUTTON, callback_data=f"connect_pc|{key_name}"),
                    InlineKeyboardButton(text=TV_BUTTON, callback_data=f"connect_tv|{key_name}"),
                )
            else:
                builder.row(
                    InlineKeyboardButton(
                        text=CONNECT_DEVICE,
                        callback_data=f"connect_device|{key_name}",
                    )
                )

        if QRCODE:
            builder.row(
                InlineKeyboardButton(
                    text=QR,
                    callback_data=f"show_qr|{key_name}",
                )
            )
        if ENABLE_DELETE_KEY_BUTTON:
            builder.row(
                InlineKeyboardButton(text=RENEW, callback_data=f"renew_key|{key_name}"),
                InlineKeyboardButton(text=DELETE, callback_data=f"delete_key|{key_name}"),
            )
        else:
            builder.row(InlineKeyboardButton(text=RENEW_FULL, callback_data=f"renew_key|{key_name}"))

        if USE_COUNTRY_SELECTION:
            builder.row(InlineKeyboardButton(text=CHANGE_LOCATION, callback_data=f"change_location|{key_name}"))

        if TOGGLE_CLIENT:
            builder.row(
                InlineKeyboardButton(
                    text=FREEZE,
                    callback_data=f"freeze_subscription|{key_name}",
                )
            )

        builder.row(InlineKeyboardButton(text=BACK, callback_data="view_keys"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text=response_message,
            reply_markup=builder.as_markup(),
            media_path=image_path,
        )
    except Exception as e:
        await handle_error(
            tg_id,
            callback_query,
            f"Ошибка при получении информации о ключе: {e}",
        )