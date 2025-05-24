import os
from io import BytesIO

import qrcode
from aiogram import F, Router, types
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    CONNECT_ANDROID,
    CONNECT_IOS,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    INSTRUCTIONS_BUTTON,
)
from database.models import Key
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
from logger import logger

router = Router()


@router.callback_query(F.data.startswith("connect_device|"))
async def handle_connect_device(callback_query: CallbackQuery):
    try:
        key_name = callback_query.data.split("|")[1]

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text=IPHONE, callback_data=f"connect_ios|{key_name}")
        )
        builder.row(
            InlineKeyboardButton(
                text=ANDROID, callback_data=f"connect_android|{key_name}"
            )
        )
        builder.row(
            InlineKeyboardButton(text=PC, callback_data=f"connect_pc|{key_name}")
        )
        builder.row(
            InlineKeyboardButton(text=TV, callback_data=f"connect_tv|{key_name}")
        )
        #    builder.row(InlineKeyboardButton(text=ROUTER, callback_data=f"connect_router|{key_name}"))
        builder.row(
            InlineKeyboardButton(text=BACK, callback_data=f"view_key|{key_name}")
        )

        await edit_or_send_message(
            target_message=callback_query.message,
            text=CHOOSE_DEVICE_TEXT,
            reply_markup=builder.as_markup(),
            media_path=None,
        )
    except Exception as e:
        await callback_query.message.answer("❌ Ошибка при показе меню подключения.")
        logger.error(f"Ошибка в handle_connect_device: {e}")


@router.callback_query(F.data.startswith("connect_phone|"))
async def process_callback_connect_phone(
    callback_query: CallbackQuery, session: AsyncSession
):
    email = callback_query.data.split("|")[1]

    try:
        result = await session.execute(select(Key.key).where(Key.email == email))
        row = result.scalar_one_or_none()

        if not row:
            await callback_query.message.answer("❌ Ошибка: ключ не найден.")
            return

        key_link = row

    except Exception as e:
        logger.error(f"Ошибка при получении ключа для {email}: {e}")
        await callback_query.message.answer("❌ Произошла ошибка. Попробуйте позже.")
        return

    description = SUBSCRIPTION_DESCRIPTION.format(key_link=key_link)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=DOWNLOAD_IOS_BUTTON, url=DOWNLOAD_IOS),
        InlineKeyboardButton(text=DOWNLOAD_ANDROID_BUTTON, url=DOWNLOAD_ANDROID),
    )
    builder.row(
        InlineKeyboardButton(text=IMPORT_IOS, url=f"{CONNECT_IOS}{key_link}"),
        InlineKeyboardButton(text=IMPORT_ANDROID, url=f"{CONNECT_ANDROID}{key_link}"),
    )
    if INSTRUCTIONS_BUTTON:
        builder.row(
            InlineKeyboardButton(text=MANUAL_INSTRUCTIONS, callback_data="instructions")
        )
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{email}"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=description,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_ios|"))
async def process_callback_connect_ios(
    callback_query: CallbackQuery, session: AsyncSession
):
    email = callback_query.data.split("|")[1]

    try:
        result = await session.execute(select(Key.key).where(Key.email == email))
        key_link = result.scalar_one_or_none()

        if not key_link:
            await callback_query.message.answer("❌ Ошибка: ключ не найден.")
            return

    except Exception as e:
        logger.error(f"Ошибка при получении ключа для {email} (iOS): {e}")
        await callback_query.message.answer("❌ Произошла ошибка. Попробуйте позже.")
        return

    description = IOS_DESCRIPTION_TEMPLATE.format(key_link=key_link)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=DOWNLOAD_IOS_BUTTON, url=DOWNLOAD_IOS))
    builder.row(InlineKeyboardButton(text=IMPORT_IOS, url=f"{CONNECT_IOS}{key_link}"))
    if INSTRUCTIONS_BUTTON:
        builder.row(
            InlineKeyboardButton(text=MANUAL_INSTRUCTIONS, callback_data="instructions")
        )
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data=f"connect_device|{email}")
    )
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=description,
        reply_markup=builder.as_markup(),
        media_path=None,
    )


@router.callback_query(F.data.startswith("connect_android|"))
async def process_callback_connect_android(
    callback_query: CallbackQuery, session: AsyncSession
):
    email = callback_query.data.split("|")[1]

    try:
        result = await session.execute(select(Key.key).where(Key.email == email))
        key_link = result.scalar_one_or_none()

        if not key_link:
            await callback_query.message.answer("❌ Ошибка: ключ не найден.")
            return

    except Exception as e:
        logger.error(f"Ошибка при получении ключа для {email} (Android): {e}")
        await callback_query.message.answer("❌ Произошла ошибка. Попробуйте позже.")
        return

    description = ANDROID_DESCRIPTION_TEMPLATE.format(key_link=key_link)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=DOWNLOAD_ANDROID_BUTTON, url=DOWNLOAD_ANDROID)
    )
    builder.row(
        InlineKeyboardButton(text=IMPORT_ANDROID, url=f"{CONNECT_ANDROID}{key_link}")
    )
    if INSTRUCTIONS_BUTTON:
        builder.row(
            InlineKeyboardButton(text=MANUAL_INSTRUCTIONS, callback_data="instructions")
        )
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data=f"connect_device|{email}")
    )
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
            await callback_query.message.answer(
                "❌ У этой подписки отсутствует ссылка для подключения."
            )
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
        builder.row(
            InlineKeyboardButton(text=BACK, callback_data=f"view_key|{record.email}")
        )
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text="🔲 <b>Ваш QR-код для подключения</b>",
            reply_markup=builder.as_markup(),
            media_path=qr_path,
        )

        os.remove(qr_path)

    except Exception as e:
        logger.error(f"Ошибка при генерации QR: {e}", exc_info=True)
        await callback_query.message.answer("❌ Произошла ошибка при создании QR-кода.")
