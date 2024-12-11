import os

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import NEWS_MESSAGE, RENEWAL_PLANS
from database import get_balance, get_key_count, get_referral_stats
from handlers.texts import get_referral_link, invite_message_send, profile_message_send
from keyboards.profile_kb import build_profile_kb, build_profile_back_kb

router = Router()


@router.callback_query(F.data == "profile")
async def process_callback_view_profile(callback_query: types.CallbackQuery, state: FSMContext, admin: bool):
    chat_id = callback_query.message.chat.id
    username = callback_query.from_user.full_name
    image_path = os.path.join("img", "pic.jpg")
    key_count = await get_key_count(chat_id)
    balance = await get_balance(chat_id) or 0

    profile_message = profile_message_send(username, chat_id, int(balance), key_count)

    if key_count == 0:
        profile_message += "\n<pre>🔧 <i>Нажмите кнопку ➕ Устройство, чтобы настроить VPN-подключение</i></pre>"
    else:
        profile_message += f"\n<pre>🔧 <i>{NEWS_MESSAGE}</i></pre>"

    # Build profile keyboard
    kb = build_profile_kb(admin)

    # Answer message
    if os.path.isfile(image_path):
        with open(image_path, "rb") as image_file:
            await callback_query.message.answer_photo(
                photo=BufferedInputFile(image_file.read(), filename="pic.jpg"),
                caption=profile_message,
                reply_markup=kb,
            )
    else:
        await callback_query.message.answer(
            text=profile_message,
            reply_markup=kb,
        )


@router.callback_query(F.data == "view_tariffs")
async def view_tariffs_handler(callback_query: types.CallbackQuery):
    # Путь к изображению
    image_path = os.path.join("img", "tariffs.jpg")  # Убедитесь, что этот путь правильный

    # Формируем текст с тарифами
    tariffs_message = (
        "<b>🚀 Доступные тарифы VPN:</b>\n\n"
        + "\n".join(
            [
                f"{months} {'месяц' if months == '1' else 'месяца' if int(months) in [2, 3, 4] else 'месяцев'}: "
                f"{RENEWAL_PLANS[months]['price']} "
                f"{'💳' if months == '1' else '🌟' if months == '3' else '🔥' if months == '6' else '🚀'} рублей"
                for months in sorted(RENEWAL_PLANS.keys(), key=int)
            ]
        )
    )

    # Build back keyboard
    kb = build_profile_back_kb()

    # Проверяем наличие файла изображения
    if os.path.isfile(image_path):
        # Если изображение существует, отправляем его
        with open(image_path, "rb") as image_file:
            await callback_query.message.answer_photo(
                photo=BufferedInputFile(image_file.read(), filename="tariffs.jpg"),
                caption=tariffs_message,
                reply_markup=kb,
            )
    else:
        # Если изображения нет, просто отправляем текст
        await callback_query.message.answer(
            text=tariffs_message,
            reply_markup=kb,
        )


@router.callback_query(F.data == "invite")
async def invite_handler(callback_query: types.CallbackQuery):
    chat_id = callback_query.message.chat.id
    referral_link = get_referral_link(chat_id)

    referral_stats = await get_referral_stats(chat_id)
    invite_message = invite_message_send(referral_link, referral_stats)
    image_path = os.path.join("img", "pic_invite.jpg")

    # Build back keyboard
    kb = build_profile_back_kb()

    if os.path.isfile(image_path):
        with open(image_path, "rb") as image_file:
            await callback_query.message.answer_photo(
                photo=BufferedInputFile(image_file.read(), filename="pic_invite.jpg"),
                caption=invite_message,
                reply_markup=kb,
            )
    else:
        await callback_query.message.answer(
            text=invite_message,
            reply_markup=kb,
        )
