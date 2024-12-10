import os

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import NEWS_MESSAGE, RENEWAL_PLANS
from database import get_balance, get_key_count, get_referral_stats
from handlers.texts import get_referral_link, invite_message_send, profile_message_send

router = Router()


@router.callback_query(F.data == "profile")
async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext, admin: bool):
    chat_id = callback_query.message.chat.id
    username = callback_query.from_user.full_name
    image_path = os.path.join("img", "pic.jpg")
    key_count = await get_key_count(chat_id)
    balance = await get_balance(chat_id)
    if balance is None:
        balance = 0

    profile_message = profile_message_send(username, chat_id, int(balance), key_count)

    if key_count == 0:
        profile_message += "\n<pre>🔧 <i>Нажмите кнопку ➕ Устройство, чтобы настроить VPN-подключение</i></pre>"
    else:
        profile_message += f"\n<pre>🔧 <i>{NEWS_MESSAGE}</i></pre>"

    builder = InlineKeyboardBuilder()
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
    builder.row(InlineKeyboardButton(text="💡 Тарифы", callback_data="view_tariffs"))
    if admin:
        builder.row(InlineKeyboardButton(text="🔧 Администратор", callback_data="admin"))
    builder.row(InlineKeyboardButton(text="⬅️ Главное меню", callback_data="start"))

    if os.path.isfile(image_path):
        with open(image_path, "rb") as image_file:
            await callback_query.message.answer_photo(
                photo=BufferedInputFile(image_file.read(), filename="pic.jpg"),
                caption=profile_message,
                reply_markup=builder.as_markup(),
            )
    else:
        await callback_query.message.answer(
            text=profile_message,
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "view_tariffs")
async def view_tariffs_handler(callback_query: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await callback_query.message.answer(
        "<b>🚀 Доступные тарифы VPN:</b>\n\n"
        + "\n".join(
            [
                f"{months} {'месяц' if months == '1' else 'месяца' if int(months) in [2, 3, 4] else 'месяцев'}: "
                f"{RENEWAL_PLANS[months]['price']} "
                f"{'💳' if months == '1' else '🌟' if months == '3' else '🔥' if months == '6' else '🚀'} рублей"
                for months in sorted(RENEWAL_PLANS.keys(), key=int)
            ]
        ),
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
