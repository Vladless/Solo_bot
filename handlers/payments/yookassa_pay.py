import uuid
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web
from yookassa import Configuration, Payment

from config import YOOKASSA_ENABLE, YOOKASSA_SECRET_KEY, YOOKASSA_SHOP_ID
from database import add_connection, add_payment, check_connection_exists, get_key_count, update_balance
from handlers.payments.utils import send_payment_success_notification
from keyboards.common_kb import build_back_kb
from keyboards.payments.pay_common_kb import build_payment_kb, build_invoice_kb
from logger import logger

router = Router()

if YOOKASSA_ENABLE:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY
    logger.debug(f"Account ID: {YOOKASSA_SHOP_ID}")
    logger.debug(f"Secret Key: {YOOKASSA_SECRET_KEY}")


class ReplenishBalanceState(StatesGroup):
    choosing_amount_yookassa = State()
    waiting_for_payment_confirmation_yookassa = State()
    entering_custom_amount_yookassa = State()


@router.callback_query(F.data == "pay_yookassa")
async def process_callback_pay_yookassa(callback_query: types.CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.message.chat.id

    # Check keys count
    key_count = await get_key_count(tg_id)
    if key_count == 0:
        exists = await check_connection_exists(tg_id)
        if not exists:
            await add_connection(tg_id, balance=0.0, trial=0, session=session)

    # Build keyboard
    kb = build_payment_kb("yookassa")

    # Answer message
    await callback_query.message.answer(
        text="Выберите сумму пополнения:",
        reply_markup=kb,
    )

    # Set state
    await state.set_state(ReplenishBalanceState.choosing_amount_yookassa)


@router.callback_query(F.data.startswith("yookassa_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split("|", 1)

    if len(data) != 2:
        return

    amount_str = data[1]
    try:
        amount = int(amount_str)
    except ValueError:
        return

    await state.update_data(amount=amount)
    await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_yookassa)

    # state_data = await state.get_data()
    customer_name = callback_query.from_user.full_name
    customer_id = callback_query.message.chat.id

    customer_email = f"{customer_id}@solo.net"

    payment = Payment.create(
        {
            "amount": {"value": str(amount), "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "return_url": "https://pocomacho.ru/success.html",
            },
            "capture": True,
            "description": "Пополнение баланса",
            "receipt": {
                "customer": {
                    "full_name": customer_name,
                    "email": customer_email,
                    "phone": "79000000000",
                },
                "items": [
                    {
                        "description": "Пополнение баланса",
                        "quantity": "1.00",
                        "amount": {"value": str(amount), "currency": "RUB"},
                        "vat_code": 6,
                    }
                ],
            },
            "metadata": {"user_id": customer_id},
        },
        uuid.uuid4(),
    )

    if payment["status"] == "pending":
        payment_url = payment["confirmation"]["confirmation_url"]

        # Build keyboard
        kb = build_invoice_kb(amount, payment_url)

        # Answer message
        await callback_query.message.answer(
            text=f"Вы выбрали пополнение на {amount} рублей.",
            reply_markup=kb,
        )
    else:
        await callback_query.message.answer("Ошибка при создании платежа.")


@router.callback_query(F.data == "enter_custom_amount_yookassa")
async def process_enter_custom_amount(callback_query: types.CallbackQuery, state: FSMContext):
    # Build keyboard
    kb = build_back_kb("pay_yookassa")

    # Answer message
    await callback_query.message.answer(
        "Пожалуйста, введите сумму пополнения.",
        reply_markup=kb,
    )

    # Set state
    await state.set_state(ReplenishBalanceState.entering_custom_amount_yookassa)


@router.message(ReplenishBalanceState.entering_custom_amount_yookassa)
async def process_custom_amount_input(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        amount = int(message.text)
        if amount <= 0:
            await message.answer(
                text="Сумма должна быть больше нуля. Пожалуйста, введите сумму еще раз:"
            )
            return

        await state.update_data(amount=amount)
        await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_yookassa)

        try:
            payment = Payment.create(
                {
                    "amount": {"value": str(amount), "currency": "RUB"},
                    "confirmation": {
                        "type": "redirect",
                        "return_url": "https://pocomacho.ru/success.html",
                    },
                    "capture": True,
                    "description": "Пополнение баланса",
                    "receipt": {
                        "customer": {
                            "full_name": message.from_user.full_name,
                            "email": f"{message.chat.id}@solo.net",
                            "phone": "79000000000",
                        },
                        "items": [
                            {
                                "description": "Пополнение баланса",
                                "quantity": "1.00",
                                "amount": {
                                    "value": str(amount),
                                    "currency": "RUB",
                                },
                                "vat_code": 6,
                            }
                        ],
                    },
                    "metadata": {"user_id": message.chat.id},
                },
                uuid.uuid4(),
            )

            if payment["status"] == "pending":
                payment_url = payment["confirmation"]["confirmation_url"]

                # Build keyboard
                kb = build_invoice_kb(amount, payment_url)

                # Answer message
                await message.answer(
                    text=f"Вы выбрали пополнение на {amount} рублей.",
                    reply_markup=kb,
                )
            else:
                await message.answer("Ошибка при создании платежа.")

        except Exception as e:
            logger.error(f"Ошибка при создании платежа: {e}")
            await message.answer("Произошла ошибка при создании платежа.")
    else:
        await message.answer("Некорректная сумма. Пожалуйста, введите сумму еще раз:")


async def yookassa_webhook(request):
    event = await request.json()
    logger.debug(f"Webhook event received: {event}")
    if event["event"] == "payment.succeeded":
        user_id_str = event["object"]["metadata"]["user_id"]
        amount_str = event["object"]["amount"]["value"]
        try:
            user_id = int(user_id_str)
            amount = float(amount_str)
            logger.debug(f"Payment succeeded for user_id: {user_id}, amount: {amount}")
            await add_payment(int(user_id), float(amount), "yookassa")
            await update_balance(user_id, amount)
            await send_payment_success_notification(user_id, amount)
        except ValueError as e:
            logger.error(f"Ошибка конвертации user_id или amount: {e}")
            return web.Response(status=400)
    return web.Response(status=200)
