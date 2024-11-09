import os

import asyncpg
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from bot import bot
from config import APP_URL, CHANNEL_URL, DATABASE_URL, SUPPORT_CHAT_URL
from database import add_connection, add_referral, check_connection_exists, get_trial
from handlers.keys.trial_key import create_trial_key
from handlers.texts import ABOUT_VPN, INSTRUCTIONS_TRIAL, WELCOME_TEXT

router = Router()


async def send_welcome_message(chat_id: int, trial_status: int):
    image_path = os.path.join(os.path.dirname(__file__), "pic.jpg")

    builder = InlineKeyboardBuilder()
    if trial_status == 0:
        builder.add(
            InlineKeyboardButton(text="🔗 Подключить VPN", callback_data="connect_vpn")
        )
    builder.add(
        InlineKeyboardButton(text="👤 Мой профиль", callback_data="view_profile")
    )
    builder.add(InlineKeyboardButton(text="🔒 О VPN", callback_data="about_vpn"))
    builder.add(InlineKeyboardButton(text="📞 Поддержка", url=SUPPORT_CHAT_URL))
    builder.add(InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL))

    if not os.path.isfile(image_path):
        with open(image_path, "rb") as image_from_buffer:
            await bot.send_photo(
                chat_id=chat_id,
                photo=BufferedInputFile(image_from_buffer.read(), filename="pic.jpg"),
                caption=WELCOME_TEXT,
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )


@router.message(Command("start"))
async def start_command(message: Message):
    logger.info(f"Received start command with text: {message.text}")
    if "referral_" in message.text:
        referrer_tg_id = int(message.text.split("referral_")[1])
        logger.info(f"Referral ID: {referrer_tg_id}")
        if not await check_connection_exists(message.from_user.id):
            await add_connection(message.from_user.id)
            await add_referral(message.from_user.id, referrer_tg_id)
            await message.answer("Вас пригласил друг, добро пожаловать!")
        else:
            await message.answer("Вы уже зарегистрированы в системе!")

    trial_status = await get_trial(message.from_user.id)
    await send_welcome_message(message.chat.id, trial_status)


@router.callback_query(F.data == "connect_vpn")
async def handle_connect_vpn(callback_query: CallbackQuery):
    await callback_query.message.delete()
    user_id = callback_query.from_user.id

    trial_key_info = await create_trial_key(user_id)

    if "error" in trial_key_info:
        await callback_query.message.answer(trial_key_info["error"])
    else:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            result = await conn.execute(
                """
                UPDATE connections SET trial = 1 WHERE tg_id = $1
            """,
                user_id,
            )
            logger.info(f"Rows updated: {result}")

        except Exception as e:
            logger.error(f"Ошибка при обновлении trial: {e}")

        finally:
            await conn.close()

        key_message = (
            f"<b>Ваш ключ доступа:</b>\n<pre>{trial_key_info['key']}</pre>\n\n"
            f"<b>Инструкции:</b>\n{INSTRUCTIONS_TRIAL}"
        )

        builder = InlineKeyboardBuilder()
        builder.add(
            InlineKeyboardButton(text="👤 Мой профиль", callback_data="view_profile")
        )

        builder.add(
            InlineKeyboardButton(
                text="🍏 Подключить",
                url=f'{APP_URL}/?url=v2raytun://import/{trial_key_info["key"]}',
            )
        )
        builder.add(
            InlineKeyboardButton(
                text="🤖 Подключить",
                url=f'{APP_URL}/?url=v2raytun://import-sub?url={trial_key_info["key"]}',
            )
        )

        builder.add(
            InlineKeyboardButton(
                text="🍏 Скачать",
                url="https://apps.apple.com/ru/app/v2raytun/id6476628951",
            )
        )
        builder.add(
            InlineKeyboardButton(
                text="🤖 Скачать",
                url="https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru",
            )
        )

        await callback_query.message.answer(
            key_message, parse_mode="HTML", reply_markup=builder.as_markup()
        )

    await callback_query.answer()


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback_query: CallbackQuery):
    await callback_query.message.delete()

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu"))

    await callback_query.message.answer(
        ABOUT_VPN, parse_mode="HTML", reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@router.callback_query(F.data == "back_to_menu")
async def handle_back_to_menu(callback_query: CallbackQuery):
    await callback_query.message.delete()
    trial_status = await get_trial(callback_query.from_user.id)
    await send_welcome_message(callback_query.from_user.id, trial_status)
    await callback_query.answer()
