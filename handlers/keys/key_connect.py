import os
import urllib.parse

from io import BytesIO

import qrcode

from aiogram import F, Router, types
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    APP_URL,
    CONNECT_ANDROID,
    CONNECT_IOS,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    INSTRUCTIONS_BUTTON,
)
from database import get_key_details, get_subscription_link
from handlers.buttons import (
    ANDROID,
    BACK,
    DOWNLOAD_ANDROID_BUTTON,
    DOWNLOAD_IOS_BUTTON,
    IMPORT_ANDROID,
    IMPORT_IOS,
    IPHONE,
    MAIN_MENU,
    MANUAL_INSTRUCTIONS,
    PC,
    TV,
)
from handlers.keys.utils import build_key_callback, key_owned_by_user, resolve_key
from handlers.texts import (
    ANDROID_DESCRIPTION_TEMPLATE,
    CHOOSE_DEVICE_TEXT,
    IOS_DESCRIPTION_TEMPLATE,
    SUBSCRIPTION_DESCRIPTION,
)
from handlers.utils import edit_or_send_message
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import process_connect_device_menu
from logger import logger


router = Router()


def generate_key_qr_file(qr_data: str, email: str) -> str:
    """Генерация QR в файл. Вызывать через run_cpu(). Возвращает путь к файлу."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    qr_path = f"/tmp/qrcode_{email}.png"
    with open(qr_path, "wb") as f:
        f.write(buffer.read())
    return qr_path


@router.callback_query(F.data.startswith("connect_device|"), flags={"popup": True})
async def handle_connect_device(callback_query: CallbackQuery, session: AsyncSession):
    try:
        key_ref = callback_query.data.split("|", 1)[1]
        key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
        key_name = key_obj.email if key_obj else key_ref
        record = await get_key_details(session, key_name)
        if not key_owned_by_user(record, callback_query.from_user.id):
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return

        builder = InlineKeyboardBuilder()
        client_id = record.get("client_id")
        builder.row(
            InlineKeyboardButton(text=IPHONE, callback_data=build_key_callback("connect_ios", client_id, key_name))
        )
        builder.row(
            InlineKeyboardButton(text=ANDROID, callback_data=build_key_callback("connect_android", client_id, key_name))
        )
        builder.row(InlineKeyboardButton(text=PC, callback_data=build_key_callback("connect_pc", client_id, key_name)))
        builder.row(InlineKeyboardButton(text=TV, callback_data=build_key_callback("connect_tv", client_id, key_name)))
        builder.row(InlineKeyboardButton(text=BACK, callback_data=build_key_callback("view_key", client_id, key_name)))

        hook_builder = InlineKeyboardBuilder()
        hook_builder.attach(builder)

        hook_commands = await process_connect_device_menu(
            chat_id=callback_query.from_user.id, admin=False, session=session
        )
        if hook_commands:
            hook_builder = insert_hook_buttons(hook_builder, hook_commands)

        final_markup = hook_builder.as_markup()

        await edit_or_send_message(
            target_message=callback_query.message,
            text=CHOOSE_DEVICE_TEXT,
            reply_markup=final_markup,
            media_path=None,
        )
    except Exception as e:
        await callback_query.message.answer("❌ Ошибка при показе меню подключения.")
        logger.error(f"Ошибка в handle_connect_device: {e}")


@router.callback_query(F.data.startswith("connect_phone|"), flags={"popup": True})
async def process_callback_connect_phone(callback_query: CallbackQuery, session: AsyncSession):
    key_ref = callback_query.data.split("|", 1)[1]
    key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
    email = key_obj.email if key_obj else key_ref

    try:
        record = await get_key_details(session, email)
        if not key_owned_by_user(record, callback_query.from_user.id):
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return
        key_link = await get_subscription_link(session, email)
        if not key_link:
            await callback_query.message.answer("❌ Ошибка: ключ не найден.")
            return
    except Exception as e:
        logger.error(f"Ошибка при получении ссылки для {email}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка. Попробуйте позже.")
        return

    description = SUBSCRIPTION_DESCRIPTION.format(key_link=key_link)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=DOWNLOAD_IOS_BUTTON, url=DOWNLOAD_IOS),
        InlineKeyboardButton(text=DOWNLOAD_ANDROID_BUTTON, url=DOWNLOAD_ANDROID),
    )
    if key_link and "happ://crypt" in key_link:
        processed_link = urllib.parse.quote(key_link, safe="")
        crypto_url = f"{APP_URL}/?url={processed_link}"
        builder.row(
            InlineKeyboardButton(text=IMPORT_IOS, url=crypto_url),
            InlineKeyboardButton(text=IMPORT_ANDROID, url=crypto_url),
        )
    else:
        processed_link = key_link
        builder.row(
            InlineKeyboardButton(text=IMPORT_IOS, url=f"{CONNECT_IOS}{processed_link}"),
            InlineKeyboardButton(text=IMPORT_ANDROID, url=f"{CONNECT_ANDROID}{processed_link}"),
        )
    if INSTRUCTIONS_BUTTON:
        builder.row(InlineKeyboardButton(text=MANUAL_INSTRUCTIONS, callback_data="instructions"))
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data=build_key_callback("view_key", record.get("client_id"), email))
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=description,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_ios|"), flags={"popup": True})
async def process_callback_connect_ios(callback_query: CallbackQuery, session: AsyncSession):
    key_ref = callback_query.data.split("|", 1)[1]
    key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
    email = key_obj.email if key_obj else key_ref

    try:
        record = await get_key_details(session, email)
        if not key_owned_by_user(record, callback_query.from_user.id):
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return
        key_link = await get_subscription_link(session, email)
        if not key_link:
            await callback_query.message.answer("❌ Ошибка: ключ не найден.")
            return
    except Exception as e:
        logger.error(f"Ошибка при получении ссылки для {email} (iOS): {e}")
        await callback_query.message.answer("❌ Произошла ошибка. Попробуйте позже.")
        return

    description = IOS_DESCRIPTION_TEMPLATE.format(key_link=key_link)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=DOWNLOAD_IOS_BUTTON, url=DOWNLOAD_IOS))

    if key_link and "happ://crypt" in key_link:
        processed_link = urllib.parse.quote(key_link, safe="")
        ios_url = f"{APP_URL}/?url={processed_link}"
    else:
        processed_link = key_link
        ios_url = f"{CONNECT_IOS}{processed_link}"

    builder.row(InlineKeyboardButton(text=IMPORT_IOS, url=ios_url))
    if INSTRUCTIONS_BUTTON:
        builder.row(InlineKeyboardButton(text=MANUAL_INSTRUCTIONS, callback_data="instructions"))
    builder.row(
        InlineKeyboardButton(
            text=BACK, callback_data=build_key_callback("connect_device", record.get("client_id"), email)
        )
    )
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=description,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_android|"), flags={"popup": True})
async def process_callback_connect_android(callback_query: CallbackQuery, session: AsyncSession):
    key_ref = callback_query.data.split("|", 1)[1]
    key_obj = await resolve_key(session, callback_query.from_user.id, key_ref)
    email = key_obj.email if key_obj else key_ref

    try:
        record = await get_key_details(session, email)
        if not key_owned_by_user(record, callback_query.from_user.id):
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return
        key_link = await get_subscription_link(session, email)
        if not key_link:
            await callback_query.message.answer("❌ Ошибка: ключ не найден.")
            return
    except Exception as e:
        logger.error(f"Ошибка при получении ссылки для {email} (Android): {e}")
        await callback_query.message.answer("❌ Произошла ошибка. Попробуйте позже.")
        return

    description = ANDROID_DESCRIPTION_TEMPLATE.format(key_link=key_link)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=DOWNLOAD_ANDROID_BUTTON, url=DOWNLOAD_ANDROID))

    if key_link and "happ://crypt" in key_link:
        processed_link = urllib.parse.quote(key_link, safe="")
        android_url = f"{APP_URL}/?url={processed_link}"
    else:
        processed_link = key_link
        android_url = f"{CONNECT_ANDROID}{processed_link}"

    builder.row(InlineKeyboardButton(text=IMPORT_ANDROID, url=android_url))
    if INSTRUCTIONS_BUTTON:
        builder.row(InlineKeyboardButton(text=MANUAL_INSTRUCTIONS, callback_data="instructions"))
    builder.row(
        InlineKeyboardButton(
            text=BACK, callback_data=build_key_callback("connect_device", record.get("client_id"), email)
        )
    )
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=description,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("show_qr|"), flags={"popup": True})
async def show_qr_code(callback_query: types.CallbackQuery, session: AsyncSession):
    try:
        key_ref = callback_query.data.split("|", 1)[1]
        record = await resolve_key(session, callback_query.from_user.id, key_ref)

        if not record:
            await callback_query.message.answer("❌ Подписка не найдена.")
            return
        if record.tg_id != callback_query.from_user.id:
            await callback_query.answer("Доступ запрещён.", show_alert=True)
            return

        qr_data = record.key or record.remnawave_link
        if not qr_data:
            await callback_query.message.answer("❌ У этой подписки отсутствует ссылка для подключения.")
            return

        from core.executor import run_cpu

        qr_path = await run_cpu(generate_key_qr_file, qr_data, record.email)

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text=BACK,
                callback_data=build_key_callback("view_key", record.client_id, record.email),
            )
        )
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text="🔲 <b>Ваш QR-код для подключения</b>",
            reply_markup=builder.as_markup(),
            media_path=qr_path,
            disable_cache=True,
        )

        os.remove(qr_path)

    except Exception as e:
        logger.error(f"Ошибка при генерации QR: {e}", exc_info=True)
        await callback_query.message.answer("❌ Произошла ошибка при создании QR-кода.")
