import uuid
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from config import YOOMONEY_ENABLE, YOOMONEY_ID, YOOMONEY_SECRET_KEY

from database import (
    add_connection,
    add_payment,
    check_connection_exists,
    get_key_count,
    update_balance,
)
from handlers.payments.utils import send_payment_success_notification
from handlers.texts import PAYMENT_OPTIONS
from logger import logger
import hashlib

router = Router()

if YOOMONEY_ENABLE:
    logger.debug(f"Account ID: {YOOMONEY_ID}")


class ReplenishBalanceState(StatesGroup):
    choosing_amount_yoomoney = State()
    waiting_for_payment_confirmation_yoomoney = State()
    entering_custom_amount_yoomoney = State()


@router.callback_query(F.data == "pay_yoomoney")
async def process_callback_pay_yoomoney(
    callback_query: types.CallbackQuery, state: FSMContext, session: Any
):
    tg_id = callback_query.message.chat.id

    builder = InlineKeyboardBuilder()

    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'yoomoney_{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'yoomoney_{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'yoomoney_{PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="💰 Ввести свою сумму",
            callback_data="enter_custom_amount_yoomoney",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"))

    key_count = await get_key_count(tg_id)

    if key_count == 0:
        exists = await check_connection_exists(tg_id)
        if not exists:
            await add_connection(tg_id, balance=0.0, trial=0, session=session)

    await callback_query.message.answer(
        text="Выберите сумму пополнения:",
        reply_markup=builder.as_markup(),
    )

    await state.set_state(ReplenishBalanceState.choosing_amount_yoomoney)


@router.callback_query(F.data.startswith("yoomoney_amount"))
async def process_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext
):
    data = callback_query.data.split("|", 1)

    if len(data) != 2:
        return

    amount_str = data[1]
    try:
        amount = int(amount_str)
    except ValueError:
        return

    await state.update_data(amount=amount)
    await state.set_state(
        ReplenishBalanceState.waiting_for_payment_confirmation_yoomoney
    )

    # state_data = await state.get_data()
    #customer_name = callback_query.from_user.full_name
    customer_id = callback_query.message.chat.id
    account_id = YOOMONEY_ID
    payment_url = f"https://yoomoney.ru/quickpay/confirm.xml?receiver={account_id}&quickpay-form=shop&targets=&sum={amount}&paymentType=PC&comment=Пополнение баланса&label={customer_id}"

    confirm_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Пополнить", url=payment_url)],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="pay")],
            ]
        )

    await callback_query.message.answer(
            text=f"Вы выбрали пополнение на {amount} рублей.",
            reply_markup=confirm_keyboard,
        )


async def yoomoney_webhook(request: web.Request):
    data = await request.post()
    logger.debug(f"Webhook event received: {data}")

    user_id_str = data.get("label")
    amount_str = data.get("withdraw_amount")
    notification_secret = YOOMONEY_SECRET_KEY
    sha1_hash = data.get("sha1_hash")

    # Строим строку для хэширования
    string_to_hash = f"{data.get('notification_type')}&{data.get('operation_id')}&{data.get('amount')}&{data.get('currency')}&{data.get('datetime')}&{data.get('sender')}&{data.get('codepro')}&{notification_secret}&{user_id_str}"

    # Высчитываем хэш
    calculated_hash = hashlib.sha1(string_to_hash.encode('utf-8')).hexdigest()

    # Проверяем хэш
    if calculated_hash != sha1_hash:
        logger.error("Проверка хэша не пройдена")
        return web.Response(status=400)

    try:
        user_id = int(user_id_str)
        amount = float(amount_str)
        logger.debug(f"Payment succeeded for user_id: {user_id}, amount: {amount}")
        await add_payment(user_id, amount, "yoomoney")
        await update_balance(user_id, amount)
        await send_payment_success_notification(user_id, amount)
    except ValueError as e:
        logger.error(f"Ошибка конвертации user_id или amount: {e}")
        return web.Response(status=400)

    return web.Response(status=200)


@router.callback_query(F.data == "enter_custom_amount_yoomoney")
async def process_enter_custom_amount(
    callback_query: types.CallbackQuery, state: FSMContext
):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="pay_yoomoney"))

    await callback_query.message.answer(
        "Пожалуйста, введите сумму пополнения.",
        reply_markup=builder.as_markup(),
    )

    await state.set_state(ReplenishBalanceState.entering_custom_amount_yoomoney)


@router.message(ReplenishBalanceState.entering_custom_amount_yoomoney)
async def process_custom_amount_input(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        amount = int(message.text)
        if amount <= 0:
            await message.answer(
                "Сумма должна быть больше нуля. Пожалуйста, введите сумму еще раз:"
            )
            return

        await state.update_data(amount=amount)
        await state.set_state(
            ReplenishBalanceState.waiting_for_payment_confirmation_yoomoney
        )

        customer_id = message.chat.id
        account_id = YOOMONEY_ID
        payment_url = f"https://yoomoney.ru/quickpay/confirm.xml?receiver={account_id}&quickpay-form=shop&targets=&sum={amount}&paymentType=PC&comment=Пополнение баланса&label={customer_id}"

        confirm_keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Пополнить", url=payment_url)],
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="pay")],
                ]
            )

        await message.answer(
                text=f"Вы выбрали пополнение на {amount} рублей.",
                reply_markup=confirm_keyboard,
        ) 

    else:
        await message.answer("Некорректная сумма. Пожалуйста, введите сумму еще раз:")
