import os
from typing import Any

import aiofiles
import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import DATABASE_URL, INSTRUCTIONS_BUTTON, NEWS_MESSAGE, RENEWAL_PLANS

from database import get_balance, get_key_count, get_last_payments, get_referral_stats, get_trial
from handlers.buttons.profile import (
    ADD_SUB,
    BALANCE,
    BALANCE_HISTORY,
    GIFTS,
    INSTRUCTIONS,
    INVITE,
    MAIN_MENU,
    MY_SUBS,
    PAYMENT,
)
from handlers.texts import get_referral_link, invite_message_send, profile_message_send

router = Router()


@router.callback_query(F.data == "profile")
@router.message(F.text == "/profile")
async def process_callback_view_profile(
    callback_query_or_message: Message | CallbackQuery,
    state: FSMContext,
    admin: bool,
):
    if isinstance(callback_query_or_message, CallbackQuery):
        chat_id = callback_query_or_message.message.chat.id
        username = callback_query_or_message.from_user.full_name
        is_callback = True
    elif isinstance(callback_query_or_message, Message):
        chat_id = callback_query_or_message.chat.id
        username = callback_query_or_message.from_user.full_name
        is_callback = False

    image_path = os.path.join("img", "profile.jpg")
    key_count = await get_key_count(chat_id)
    balance = await get_balance(chat_id)
    if balance is None:
        balance = 0

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        trial_status = await get_trial(chat_id, conn)

        profile_message = profile_message_send(username, chat_id, int(balance), key_count)

        if key_count == 0:
            profile_message += (
                "\n<blockquote>🔧 <i>Нажмите кнопку ➕ Подписка, чтобы настроить VPN-подключение</i></blockquote>"
            )
        else:
            profile_message += f"\n<blockquote> <i>{NEWS_MESSAGE}</i></blockquote>"

        builder = InlineKeyboardBuilder()

        if trial_status == 0 or key_count == 0:
            builder.row(InlineKeyboardButton(text=ADD_SUB, callback_data="create_key"))
        else:
            builder.row(InlineKeyboardButton(text=MY_SUBS, callback_data="view_keys"))

        builder.row(
            InlineKeyboardButton(
                text=BALANCE,
                callback_data="balance",
            )
        )
        builder.row(
            InlineKeyboardButton(text=INVITE, callback_data="invite"),
            InlineKeyboardButton(text=GIFTS, callback_data="gifts"),
        )
        if INSTRUCTIONS_BUTTON:
            builder.row(
                InlineKeyboardButton(text=INSTRUCTIONS, callback_data="instructions"),
            )
        if admin:
            builder.row(InlineKeyboardButton(text="🔧 Администратор", callback_data="admin"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="start"))

        if os.path.isfile(image_path):
            async with aiofiles.open(image_path, "rb") as image_file:
                image_data = await image_file.read()
                if is_callback:
                    await callback_query_or_message.message.answer_photo(
                        photo=BufferedInputFile(image_data, filename="profile.jpg"),
                        caption=profile_message,
                        reply_markup=builder.as_markup(),
                    )
                else:
                    await callback_query_or_message.answer_photo(
                        photo=BufferedInputFile(image_data, filename="profile.jpg"),
                        caption=profile_message,
                        reply_markup=builder.as_markup(),
                    )
        else:
            if is_callback:
                await callback_query_or_message.message.answer(
                    text=profile_message,
                    reply_markup=builder.as_markup(),
                )
            else:
                await callback_query_or_message.answer(
                    text=profile_message,
                    reply_markup=builder.as_markup(),
                )
    finally:
        await conn.close()


@router.callback_query(F.data == "balance")
async def balance_handler(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=BALANCE_HISTORY, callback_data="balance_history"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await callback_query.message.answer("💰 Управление балансом:", reply_markup=builder.as_markup())


@router.callback_query(F.data == "balance_history")
async def balance_history_handler(callback_query: CallbackQuery, session: Any):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    records = await get_last_payments(callback_query.from_user.id, session)

    if records:
        history_text = "📊 <b>Последние 3 операции с балансом:</b>\n\n"
        for record in records:
            amount = record["amount"]
            payment_system = record["payment_system"]
            status = record["status"]
            date = record["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            history_text += (
                f"<b>Сумма:</b> {amount}₽\n"
                f"<b>Способ оплаты:</b> {payment_system}\n"
                f"<b>Статус:</b> {status}\n"
                f"<b>Дата:</b> {date}\n\n"
            )
    else:
        history_text = "❌ У вас пока нет операций с балансом."

    await callback_query.message.answer(history_text, reply_markup=builder.as_markup())


@router.message(F.text == "/tariffs")
@router.callback_query(F.data == "view_tariffs")
async def view_tariffs_handler(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    image_path = os.path.join("img", "tariffs.jpg")

    tariffs_message = "<b>🚀 Доступные тарифы VPN:</b>\n\n" + "\n".join(
        [
            f"{months} {'месяц' if months == '1' else 'месяца' if int(months) in [2, 3, 4] else 'месяцев'}: "
            f"{RENEWAL_PLANS[months]['price']} "
            f"{'💳' if months == '1' else '🌟' if months == '3' else '🔥' if months == '6' else '🚀'} рублей"
            for months in sorted(RENEWAL_PLANS.keys(), key=int)
        ]
    )

    if os.path.isfile(image_path):
        async with aiofiles.open(image_path, "rb") as image_file:
            image_data = await image_file.read()
            await callback_query.message.answer_photo(
                photo=BufferedInputFile(image_data, filename="tariffs.jpg"),
                caption=tariffs_message,
                reply_markup=builder.as_markup(),
            )
    else:
        await callback_query.message.answer(
            text=tariffs_message,
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "invite")
async def invite_handler(callback_query: CallbackQuery):
    chat_id = callback_query.message.chat.id
    referral_link = get_referral_link(chat_id)
    referral_stats = await get_referral_stats(chat_id)
    invite_text = f"\nПриглашаю тебя пользоваться действительно быстрым VPN вместе:\n\n{referral_link}"

    invite_message = invite_message_send(referral_link, referral_stats)
    image_path = os.path.join("img", "pic_invite.jpg")

    builder = InlineKeyboardBuilder()
    builder.button(
        text="📢 Поделиться",
        switch_inline_query=invite_text
    )
    builder.button(text="👤 Личный кабинет", callback_data="profile")
    builder.adjust(1)

    if os.path.isfile(image_path):
        async with aiofiles.open(image_path, "rb") as image_file:
            image_data = await image_file.read()
            await callback_query.message.answer_photo(
                photo=BufferedInputFile(image_data, filename="pic_invite.jpg"),
                caption=invite_message,
                reply_markup=builder.as_markup(),
            )
    else:
        await callback_query.message.answer(
            text=invite_message,
            reply_markup=builder.as_markup(),
        )