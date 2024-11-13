import hashlib
import hmac
import logging
import time
import uuid

import requests
from aiogram import Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

from bot import bot
from config import FREEKASSA_API_KEY, FREEKASSA_SHOP_ID
from database import update_balance
from handlers.texts import PAYMENT_OPTIONS

router = Router()
logging.basicConfig(level=logging.DEBUG)


class ReplenishBalanceState(StatesGroup):
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


def generate_signature(params, api_key):
    sorted_params = {k: params[k] for k in sorted(params)}
    sign_string = "|".join(str(value) for value in sorted_params.values())
    return hmac.new(api_key.encode(), sign_string.encode(), hashlib.sha256).hexdigest()


async def create_payment(user_id, amount, email, ip):
    payment_id = str(uuid.uuid4())
    nonce = int(time.time() * 1000)
    params = {
        "shopId": FREEKASSA_SHOP_ID,
        "amount": amount,
        "currency": "RUB",
        "paymentId": payment_id,
        "email": email,
        "ip": ip,
        "i": 6,
        "nonce": nonce,
    }
    params["signature"] = generate_signature(params, FREEKASSA_API_KEY)

    try:
        response = requests.post(
            "https://api.freekassa.com/v1/orders/create", json=params
        )
        response_data = response.json()

        logging.debug(f"Ответ от FreeKassa при создании платежа: {response_data}")

        if response_data.get("type") == "success":
            return response_data["location"]
        else:
            logging.error(f"Ошибка создания платежа: {response_data}")
            return None

    except Exception as e:
        logging.error(f"Ошибка запроса к FreeKassa: {e}")
        return None


async def send_payment_success_notification(user_id, amount):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"Ваш баланс успешно пополнен на {amount} рублей. Спасибо за оплату!",
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке уведомления пользователю {user_id}: {e}")


async def freekassa_webhook(request):
    data = await request.json()
    logging.debug(f"Получен вебхук от FreeKassa: {data}")

    logging.debug(f"Данные вебхука от FreeKassa: {data}")

    if data["status"] == "completed":
        user_id = data["metadata"]["user_id"]
        amount = float(data["amount"])

        await update_balance(user_id, amount)
        await send_payment_success_notification(user_id, amount)

    return web.Response(status=200)


@router.callback_query(lambda c: c.data == "pay_freekassa")
async def process_callback_pay_freekassa(
    callback_query: types.CallbackQuery, state: FSMContext
):
    tg_id = callback_query.from_user.id

    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=PAYMENT_OPTIONS[i]["callback_data"],
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=PAYMENT_OPTIONS[i + 1]["callback_data"],
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=PAYMENT_OPTIONS[i]["callback_data"],
                )
            )

    await bot.delete_message(
        chat_id=tg_id, message_id=callback_query.message.message_id
    )

    await bot.send_message(
        chat_id=tg_id,
        text="Выберите сумму пополнения через FreeKassa:",
        reply_markup=builder.as_markup(),
    )

    await state.set_state(ReplenishBalanceState.choosing_amount)
    await callback_query.answer()


@router.callback_query(lambda c: c.data.startswith("amount|"))
async def process_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext
):
    data = callback_query.data.split("|", 1)
    amount_str = data[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await bot.send_message(callback_query.from_user.id, "Некорректная сумма.")
        return

    user_email = f"{callback_query.from_user.id}@solo.net"
    user_ip = callback_query.message.chat.id
    payment_url = await create_payment(
        callback_query.from_user.id, amount, user_email, user_ip
    )

    if payment_url:
        confirm_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Оплатить {amount} рублей", url=payment_url
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_profile")],
            ]
        )

        await bot.send_message(
            callback_query.from_user.id,
            f"Вы выбрали оплату на {amount} рублей. Перейдите по ссылке для завершения оплаты:",
            reply_markup=confirm_keyboard,
        )
    else:
        await bot.send_message(
            callback_query.from_user.id,
            "Ошибка при создании платежа. Попробуйте позже.",
        )

    await callback_query.answer()


@router.callback_query(lambda c: c.data == "enter_custom_amount")
async def process_enter_custom_amount(
    callback_query: types.CallbackQuery, state: FSMContext
):
    await callback_query.message.edit_text(text="Введите сумму пополнения:")
    await state.set_state(ReplenishBalanceState.entering_custom_amount)
    await callback_query.answer()


@router.message(ReplenishBalanceState.entering_custom_amount)
async def process_custom_amount_input(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        amount = int(message.text)
        if amount <= 0:
            await message.answer(
                "Сумма должна быть больше нуля. Пожалуйста, введите сумму еще раз:"
            )
            return

        user_email = f"{message.from_user.id}@solo.net"
        user_ip = message.chat.id
        payment_url = await create_payment(
            message.from_user.id, amount, user_email, user_ip
        )

        if payment_url:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton("Оплатить", url=payment_url)]]
            )
            await message.answer(
                f"Вы выбрали оплату на {amount} рублей. Перейдите по ссылке для завершения оплаты:",
                reply_markup=keyboard,
            )
        else:
            await message.answer("Ошибка при создании платежа. Попробуйте позже.")

    else:
        await message.answer("Пожалуйста, введите корректную сумму.")
