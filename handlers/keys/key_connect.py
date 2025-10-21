import os
import urllib.parse

from io import BytesIO

import qrcode

from aiogram import F, Router, types
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    APP_URL,
    CONNECT_ANDROID,
    CONNECT_IOS,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    INSTRUCTIONS_BUTTON,
)
from database import Key, get_subscription_link
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
from handlers.texts import (
    ANDROID_DESCRIPTION_TEMPLATE,
    CHOOSE_DEVICE_TEXT,
    IOS_DESCRIPTION_TEMPLATE,
    SUBSCRIPTION_DESCRIPTION,
)
from handlers.utils import edit_or_send_message
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from logger import logger


router = Router()


@router.callback_query(F.data.startswith("connect_device|"))
async def handle_connect_device(callback_query: CallbackQuery, session: AsyncSession):
    try:
        key_name = callback_query.data.split("|")[1]

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=IPHONE, callback_data=f"connect_ios|{key_name}"))
        builder.row(InlineKeyboardButton(text=ANDROID, callback_data=f"connect_android|{key_name}"))
        builder.row(InlineKeyboardButton(text=PC, callback_data=f"connect_pc|{key_name}"))
        builder.row(InlineKeyboardButton(text=TV, callback_data=f"connect_tv|{key_name}"))
        builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{key_name}"))

        try:
            hook_builder = InlineKeyboardBuilder()
            hook_builder.attach(builder)

            hook_commands = await run_hooks(
                "connect_device_menu", chat_id=callback_query.from_user.id, admin=False, session=session
            )
            if hook_commands:
                hook_builder = insert_hook_buttons(hook_builder, hook_commands)

            final_markup = hook_builder.as_markup()
        except Exception as e:
            logger.warning(f"[CONNECT_DEVICE] Ошибка при применении хуков: {e}")
            final_markup = builder.as_markup()

        await edit_or_send_message(
            target_message=callback_query.message,
            text=CHOOSE_DEVICE_TEXT,
            reply_markup=final_markup,
            media_path=None,
        )
    except Exception as e:
        await callback_query.message.answer("❌ Ошибка при показе меню подключения.")
        logger.error(f"Ошибка в handle_connect_device: {e}")


@router.callback_query(F.data.startswith("connect_phone|"))
async def process_callback_connect_phone(callback_query: CallbackQuery, session: AsyncSession):
    email = callback_query.data.split("|")[1]

    try:
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
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{email}"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=description,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_ios|"))
async def process_callback_connect_ios(callback_query: CallbackQuery, session: AsyncSession):
    email = callback_query.data.split("|")[1]

    try:
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
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"connect_device|{email}"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=description,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_android|"))
async def process_callback_connect_android(callback_query: CallbackQuery, session: AsyncSession):
    email = callback_query.data.split("|")[1]

    try:
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
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"connect_device|{email}"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=description,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("show_qr|"))
async def show_qr_code(callback_query: types.CallbackQuery, session: AsyncSession):
    try:
        key_name = callback_query.data.split("|")[1]

        stmt = select(Key).where(Key.email == key_name)
        result = await session.execute(stmt)
        record = result.scalars().first()

        if not record:
            await callback_query.message.answer("❌ Подписка не найдена.")
            return

        qr_data = record.key or record.remnawave_link
        if not qr_data:
            await callback_query.message.answer("❌ У этой подписки отсутствует ссылка для подключения.")
            return

        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(qr_data)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        qr_path = f"/tmp/qrcode_{record.email}.png"
        with open(qr_path, "wb") as f:
            f.write(buffer.read())

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{record.email}"))
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
