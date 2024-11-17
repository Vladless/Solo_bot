import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import CHANNEL_URL, CONNECT_ANDROID, CONNECT_IOS, DOWNLOAD_ANDROID, DOWNLOAD_IOS, SUPPORT_CHAT_URL
from database import add_connection, add_referral, check_connection_exists, get_trial
from handlers.keys.trial_key import create_trial_key
from handlers.texts import INSTRUCTIONS_TRIAL, WELCOME_TEXT, get_about_vpn
from logger import logger

router = Router()


async def send_welcome_message(chat_id: int, trial_status: int, admin: bool):
    image_path = os.path.join(os.path.dirname(__file__), "pic.jpg")

    builder = InlineKeyboardBuilder()
    if trial_status == 0:
        builder.row(
            InlineKeyboardButton(text="🔗 Подключить VPN", callback_data="connect_vpn")
        )
    builder.row(
        InlineKeyboardButton(text="👤 Личный кабинет", callback_data="view_profile")
    )
    if admin:
        builder.row(
            InlineKeyboardButton(text="🔧 Администратор", callback_data="admin")
        )
    builder.row(
        InlineKeyboardButton(text="📞 Техническая поддержка", url=SUPPORT_CHAT_URL),
    )
    builder.row(
        InlineKeyboardButton(text="📢 Официальный канал", url=CHANNEL_URL),
    )
    builder.row(InlineKeyboardButton(text="🌐 О нашем VPN", callback_data="about_vpn"))

    if os.path.isfile(image_path):
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
async def start_command(message: Message, admin: bool):
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
    await send_welcome_message(message.chat.id, trial_status, admin)


@router.callback_query(F.data == "connect_vpn")
async def handle_connect_vpn(callback_query: CallbackQuery, session):
    await callback_query.message.delete()
    user_id = callback_query.from_user.id

    trial_key_info = await create_trial_key(user_id)

    if "error" in trial_key_info:
        await callback_query.message.answer(trial_key_info["error"])
    else:
        try:
            result = await session.execute(
                """
                UPDATE connections SET trial = 1 WHERE tg_id = $1
            """,
                user_id,
            )
            logger.info(f"Rows updated: {result}")

        except Exception as e:
            logger.error(f"Ошибка при обновлении trial: {e}")

        key_message = (
            f"🔑 <b>Ваш персональный ключ доступа:</b>\n"
            f"<pre>{trial_key_info['key']}</pre>\n\n"
            f"📋 <b>Быстрая инструкция по подключению:</b>\n{INSTRUCTIONS_TRIAL}"
        )

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="👤 Личный кабинет", callback_data="view_profile")
        )

        builder.row(
            InlineKeyboardButton(
                text="🍏 Скачать для iOS",
                url=DOWNLOAD_IOS,
            ),
            InlineKeyboardButton(
                text="🤖 Скачать для Android",
                url=DOWNLOAD_ANDROID,
            ),
        )

        builder.row(
            InlineKeyboardButton(
                text="🍏 Подключить на iOS",
                url=f'{CONNECT_IOS}{trial_key_info["key"]}',
            ),
            InlineKeyboardButton(
                text="🤖 Подключить на Android",
                url=f'{CONNECT_ANDROID}{trial_key_info["key"]}',
            ),
        )

        await callback_query.message.answer(
            key_message, parse_mode="HTML", reply_markup=builder.as_markup()
        )

    await callback_query.answer()


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback_query: CallbackQuery):
    await callback_query.message.delete()

    about_vpn_message = get_about_vpn("3.1.0_Stable")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate")
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu"))

    await callback_query.message.answer(
        about_vpn_message, parse_mode="HTML", reply_markup=builder.as_markup()
    )
    await callback_query.answer()


@router.callback_query(F.data == "back_to_menu")
async def handle_back_to_menu(callback_query: CallbackQuery):
    await callback_query.message.delete()
    trial_status = await get_trial(callback_query.from_user.id)
    await send_welcome_message(callback_query.from_user.id, trial_status)
    await callback_query.answer()
