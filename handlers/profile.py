import os

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import bot
from config import CHANNEL_URL
from database import get_balance, get_key_count, get_referral_stats
from handlers.texts import get_referral_link, invite_message_send, profile_message_send
from logger import logger

router = Router()


async def process_callback_view_profile(
    callback_query: types.CallbackQuery, state: FSMContext
):
    chat_id = callback_query.from_user.id
    username = callback_query.from_user.full_name

    image_path = os.path.join(os.path.dirname(__file__), "pic.jpg")

    try:
        key_count = await get_key_count(chat_id)
        balance = await get_balance(chat_id)
        if balance is None:
            balance = 0

        profile_message = profile_message_send(username, chat_id, balance, key_count)

        if key_count == 0:
            profile_message += "\n🔧 <i>Нажмите кнопку ➕ Устройство, чтобы настроить VPN-подключение</i>"

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL))
        builder.row(
            InlineKeyboardButton(text="➕ Устройство", callback_data="create_key"),
            InlineKeyboardButton(text="📱 Мои устройства", callback_data="view_keys"),
        )
        builder.row(
            InlineKeyboardButton(
                text="💳 Пополнить баланс",
                callback_data="pay",
            )
        )
        builder.row(
            InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="invite"),
            InlineKeyboardButton(text="📘 Инструкции", callback_data="instructions"),
        )
        builder.row(
            InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate")
        )
        builder.row(
            InlineKeyboardButton(text="⬅️ Главное меню", callback_data="back_to_menu")
        )

        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"❗ Ошибка при удалении сообщения: {e}")

        if os.path.isfile(image_path):
            with open(image_path, "rb") as image_file:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(image_file.read(), filename="pic.jpg"),
                    caption=profile_message,
                    parse_mode="HTML",
                    reply_markup=builder.as_markup(),
                )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=profile_message,
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )

    except Exception as e:
        await bot.send_message(
            chat_id, f"❗️ Не удалось загрузить профиль. Техническая ошибка: {e}"
        )

    await callback_query.answer()


@router.callback_query(F.data == "invite")
async def invite_handler(callback_query: types.CallbackQuery):
    chat_id = callback_query.from_user.id
    referral_link = get_referral_link(chat_id)

    referral_stats = await get_referral_stats(chat_id)

    invite_message = invite_message_send(referral_link, referral_stats)

    image_path = os.path.join(os.path.dirname(__file__), "pic_invite.jpg")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ Вернуться в профиль", callback_data="view_profile")
    )

    try:
        await callback_query.message.delete()
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")

    try:
        if os.path.isfile(image_path):
            with open(image_path, "rb") as image_file:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=BufferedInputFile(
                        image_file.read(), filename="pic_invite.jpg"
                    ),
                    caption=invite_message,
                    parse_mode="HTML",
                    reply_markup=builder.as_markup(),
                )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=invite_message,
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
    except Exception as e:
        await bot.send_message(
            chat_id=chat_id,
            text=f"❗️ Не удалось отправить сообщение. Техническая ошибка: {e}",
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )

    await callback_query.answer()


@router.callback_query(F.data == "view_profile")
async def view_profile_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await process_callback_view_profile(callback_query, state)
