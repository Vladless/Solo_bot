import os
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    CHANNEL_EXISTS,
    CHANNEL_URL,
    CONNECT_ANDROID,
    CONNECT_IOS,
    DONATIONS_ENABLE,
    DOWNLOAD_ANDROID,
    DOWNLOAD_IOS,
    SUPPORT_CHAT_URL,
)
from database import add_connection, add_referral, check_connection_exists, get_trial, use_trial
from handlers.keys.key_management import create_key
from handlers.keys.trial_key import create_trial_key
from handlers.texts import INSTRUCTIONS_TRIAL, WELCOME_TEXT, get_about_vpn
from logger import logger

router = Router()


@router.callback_query(F.data == "start")
async def handle_start_callback_query(
    callback_query: CallbackQuery, state: FSMContext, session: Any, admin: bool
):
    await start_command(callback_query.message, state, session, admin)


@router.message(Command("start"))
async def start_command(message: Message, state: FSMContext, session: Any, admin: bool):
    """Обрабатывает команду /start, включает логику рефералов и подарков."""
    logger.info(f"Вызвана функция start_command для пользователя {message.chat.id}")

    await state.clear()

    if message.text:
        try:
            connection_exists = await check_connection_exists(message.chat.id)
            logger.info(f"Проверка существования подключения: {connection_exists}")


            if not connection_exists:
                await add_connection(tg_id=message.chat.id, session=session)
                logger.info(f"Пользователь {message.chat.id} успешно добавлен в базу данных.")

            if "gift_" in message.text:
                logger.info(f"Обнаружена ссылка на подарок: {message.text}")
                parts = message.text.split("gift_")[1].split("_")
                gift_id = parts[0]

                recipient_tg_id = message.chat.id

                gift_info = await session.fetchrow(
                    "SELECT * FROM gifts WHERE gift_id = $1 AND is_used = FALSE", gift_id
                )

                if gift_info is None:
                    logger.warning(f"Подарок с ID {gift_id} уже был использован или не существует.")
                    await message.answer("Этот подарок уже был использован или не существует.")
                    return await show_start_menu(message, admin, session)

                if gift_info['sender_tg_id'] == recipient_tg_id:
                    logger.warning(
                        f"Пользователь {recipient_tg_id} попытался активировать подарок, который был отправлен им самим."
                    )
                    await message.answer("❌ Вы не можете получить подарок от самого себя.")
                    return await show_start_menu(message, admin, session)

                selected_months = gift_info['selected_months']
                expiry_time = gift_info['expiry_time']
                expiry_time_naive = expiry_time.replace(tzinfo=None)
                logger.info(f"Подарок с ID {gift_id} успешно найден для пользователя {recipient_tg_id}.")

                await create_key(recipient_tg_id, expiry_time_naive, state, session, message)
                logger.info(f"Ключ создан для пользователя {recipient_tg_id} на срок {selected_months} месяцев.")

                await session.execute(
                    "UPDATE gifts SET is_used = TRUE, recipient_tg_id = $1 WHERE gift_id = $2",
                    recipient_tg_id,
                    gift_id,
                )

                await message.answer(
                    f"🎉 Ваш подарок на {selected_months} {'месяц' if selected_months == 1 else 'месяца' if selected_months in [2, 3, 4] else 'месяцев'} активирован!"
                )
                logger.info(f"Подарок на {selected_months} месяцев активирован для пользователя {recipient_tg_id}.")
                return

            elif "referral_" in message.text:
                try:
                    referrer_tg_id = int(message.text.split("referral_")[1])

                    if connection_exists:
                        logger.info(
                            f"Пользователь {message.chat.id} уже зарегистрирован и не может стать рефералом."
                        )
                        await message.answer("❌ Вы уже зарегистрированы и не можете использовать реферальную ссылку.")
                        return await show_start_menu(message, admin, session)

                    if referrer_tg_id == message.chat.id:
                        logger.warning(f"Пользователь {message.chat.id} попытался стать рефералом самого себя.")
                        await message.answer("❌ Вы не можете быть рефералом самого себя.")
                        return await show_start_menu(message, admin, session)

                    existing_referral = await session.fetchrow(
                        "SELECT * FROM referrals WHERE referred_tg_id = $1", message.chat.id
                    )

                    if existing_referral:
                        logger.info(f"Реферал с ID {message.chat.id} уже существует.")
                        return await show_start_menu(message, admin, session)

                    await add_referral(message.chat.id, referrer_tg_id, session)
                    logger.info(f"Реферал {message.chat.id} использовал ссылку от пользователя {referrer_tg_id}")
                    return await show_start_menu(message, admin, session)

                except (ValueError, IndexError) as e:
                    logger.error(f"Ошибка при обработке реферальной ссылки: {e}")
                return

            else:
                logger.info(f"Пользователь {message.chat.id} зашел без реферальной ссылки или подарка.")

            await show_start_menu(message, admin, session)

        except (ValueError, IndexError) as e:
            logger.error(f"Ошибка при обработке сообщения пользователя {message.chat.id}: {e}")
            await message.answer("❌ Произошла ошибка. Пожалуйста, попробуйте снова.")
    else:
        await show_start_menu(message, admin, session)


async def show_start_menu(message: Message, admin: bool, session: Any):
    """Функция для отображения стандартного меню"""
    logger.info(f"Показываю главное меню для пользователя {message.chat.id}")
    trial_status = await get_trial(message.chat.id, session)
    image_path = os.path.join("img", "pic.jpg")

    builder = InlineKeyboardBuilder()

    if trial_status == 0:
        builder.row(InlineKeyboardButton(text="🔗 Подключить VPN", callback_data="connect_vpn"))

    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    builder.row(
        InlineKeyboardButton(text="📞 Поддержка", url=SUPPORT_CHAT_URL),
        InlineKeyboardButton(text="📢 Канал", url=CHANNEL_URL),
    )

    if admin:
        builder.row(InlineKeyboardButton(text="🔧 Администратор", callback_data="admin"))

    builder.row(InlineKeyboardButton(text="🌐 О нашем VPN", callback_data="about_vpn"))

    if os.path.isfile(image_path):
        with open(image_path, "rb") as image_from_buffer:
            await message.answer_photo(
                photo=BufferedInputFile(image_from_buffer.read(), filename="pic.jpg"),
                caption=WELCOME_TEXT,
                reply_markup=builder.as_markup(),
            )
    else:
        await message.answer(
            text=WELCOME_TEXT,
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "connect_vpn")
async def handle_connect_vpn(callback_query: CallbackQuery, session: Any):
    user_id = callback_query.message.chat.id

    trial_key_info = await create_trial_key(user_id, session)

    if "error" in trial_key_info:
        await callback_query.message.answer(trial_key_info["error"])
    else:
        await use_trial(user_id, session)

        key_message = (
            f"🔑 <b>Ваш персональный ключ доступа:</b>\n"
            f"<code>{trial_key_info['key']}</code>\n\n"
            f"📋 <b>Быстрая инструкция по подключению:</b>\n{INSTRUCTIONS_TRIAL}"
        )

        email = trial_key_info["email"]

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="💬 Поддержка", url=SUPPORT_CHAT_URL))
        builder.row(
            InlineKeyboardButton(text="🍏 Скачать для iOS", url=DOWNLOAD_IOS),
            InlineKeyboardButton(text="🤖 Скачать для Android", url=DOWNLOAD_ANDROID),
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
        builder.row(
            InlineKeyboardButton(
                text="💻 Windows/Linux", callback_data=f"connect_pc|{email}"
            )
        )
        builder.row(
            InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile")
        )

        await callback_query.message.answer(
            key_message, reply_markup=builder.as_markup()
        )


@router.callback_query(F.data == "about_vpn")
async def handle_about_vpn(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()

    if DONATIONS_ENABLE:
        builder.row(InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate"))

    builder.row(
        InlineKeyboardButton(text="📞 Техническая поддержка", url=SUPPORT_CHAT_URL),
    )
    if CHANNEL_EXISTS:
        builder.row(
            InlineKeyboardButton(text="📢 Официальный канал", url=CHANNEL_URL),
        )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="start"))

    await callback_query.message.answer(
        get_about_vpn("3.2.3-beta-gifts"), reply_markup=builder.as_markup()
    )
