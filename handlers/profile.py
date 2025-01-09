import os

import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import DATABASE_URL, NEWS_MESSAGE, RENEWAL_PLANS
from database import get_balance, get_key_count, get_referral_stats, get_trial
from handlers.buttons.profile import ADD_SUB, GIFTS, INSTRUCTIONS, INVITE, MAIN_MENU, MY_SUBS, PAYMENT
from handlers.texts import get_referral_link, invite_message_send, profile_message_send

router = Router()


@router.callback_query(F.data == "profile")
@router.message(F.text == "/profile")
async def process_callback_view_profile(
    callback_query_or_message: types.Message | types.CallbackQuery, state: FSMContext, admin: bool
):
    if isinstance(callback_query_or_message, types.CallbackQuery):
        chat_id = callback_query_or_message.message.chat.id
        username = callback_query_or_message.from_user.full_name
        is_callback = True
    elif isinstance(callback_query_or_message, types.Message):
        chat_id = callback_query_or_message.chat.id
        username = callback_query_or_message.from_user.full_name
        is_callback = False

    image_path = os.path.join("img", "pic.jpg")
    key_count = await get_key_count(chat_id)
    balance = await get_balance(chat_id)
    if balance is None:
        balance = 0

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        trial_status = await get_trial(chat_id, conn)

        profile_message = profile_message_send(username, chat_id, int(balance), key_count)

        if key_count == 0:
            profile_message += "\n<pre>🔧 <i>Нажмите кнопку ➕ Устройство, чтобы настроить VPN-подключение</i></pre>"
        else:
            profile_message += f"\n<pre> <i>{NEWS_MESSAGE}</i></pre>"

        builder = InlineKeyboardBuilder()


        if trial_status == 0 or key_count == 0:
            builder.row(
                InlineKeyboardButton(text=ADD_SUB, callback_data="create_key")
            )
        else:
            builder.row(
                InlineKeyboardButton(text=MY_SUBS, callback_data="view_keys")
            )

        builder.row(
            InlineKeyboardButton(
                text=PAYMENT,
                callback_data="pay",
            )
        )
        builder.row(
            InlineKeyboardButton(text=INVITE, callback_data="invite"),
            InlineKeyboardButton(text=GIFTS, callback_data="gifts"),
        )
        builder.row(
            InlineKeyboardButton(text=INSTRUCTIONS, callback_data="instructions"),
        )
        if admin:
            builder.row(
                InlineKeyboardButton(text="🔧 Администратор", callback_data="admin")
            )
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="start"))

        if os.path.isfile(image_path):
            with open(image_path, "rb") as image_file:
                if is_callback:
                    await callback_query_or_message.message.answer_photo(
                        photo=BufferedInputFile(image_file.read(), filename="pic.jpg"),
                        caption=profile_message,
                        reply_markup=builder.as_markup(),
                    )
                else:
                    await callback_query_or_message.answer_photo(
                        photo=BufferedInputFile(image_file.read(), filename="pic.jpg"),
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


@router.message(F.text == "/tariffs")
@router.callback_query(F.data == "view_tariffs")
async def view_tariffs_handler(callback_query: types.CallbackQuery):
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
        with open(image_path, "rb") as image_file:
            await callback_query.message.answer_photo(
                photo=BufferedInputFile(image_file.read(), filename="tariffs.jpg"),
                caption=tariffs_message,
                reply_markup=builder.as_markup(),
            )
    else:
        await callback_query.message.answer(
            text=tariffs_message,
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "invite")
async def invite_handler(callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    referral_link = get_referral_link(chat_id)

    referral_stats = await get_referral_stats(chat_id)

    invite_message = invite_message_send(referral_link, referral_stats)

    image_path = os.path.join("img", "pic_invite.jpg")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
    if os.path.isfile(image_path):
        with open(image_path, "rb") as image_file:
            await callback_query.message.answer_photo(
                photo=BufferedInputFile(image_file.read(), filename="pic_invite.jpg"),
                caption=invite_message,
                reply_markup=builder.as_markup(),
            )
    else:
        await callback_query.message.answer(
            text=invite_message,
            reply_markup=builder.as_markup(),
        )
