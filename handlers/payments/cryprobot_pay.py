from typing import Any

from aiocryptopay import AioCryptoPay, Networks
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

from config import CRYPTO_BOT_ENABLE, CRYPTO_BOT_TOKEN, RUB_TO_USDT
from database import add_connection, add_payment, check_connection_exists, get_key_count, update_balance
from handlers.payments.utils import send_payment_success_notification
from keyboards.common_kb import build_back_kb
from keyboards.payments.pay_common_kb import build_payment_kb, build_invoice_kb, build_pay_url_kb
from logger import logger

router = Router()

if CRYPTO_BOT_ENABLE:
    crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)


class ReplenishBalanceState(StatesGroup):
    choosing_amount_crypto = State()
    waiting_for_payment_confirmation_crypto = State()
    entering_custom_amount_crypto = State()


@router.callback_query(F.data == "pay_cryptobot")
async def process_callback_pay_cryptobot(callback_query: types.CallbackQuery, state: FSMContext, session: Any):
    # Check keys count
    key_count = await get_key_count(callback_query.message.chat.id)
    if key_count == 0:
        exists = await check_connection_exists(callback_query.message.chat.id)
        if not exists:
            await add_connection(tg_id=callback_query.message.chat.id, balance=0.0, trial=0, session=session)

    # Build keyboard
    kb = build_payment_kb("cryptobot")

    # Answer message
    await callback_query.message.answer(
        text="Выберите сумму пополнения:",
        reply_markup=kb,
    )

    # Set state
    await state.set_state(ReplenishBalanceState.choosing_amount_crypto)


@router.callback_query(F.data.startswith("crypto_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split("|", 1)

    if len(data) != 2:
        await callback_query.message.answer("Неверные данные для выбора суммы.")
        return

    amount_str = data[1]
    try:
        amount = int(amount_str)
    except ValueError:
        await callback_query.message.answer("Некорректная сумма.")
        return

    await state.update_data(amount=amount)
    await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_crypto)

    try:
        invoice = await crypto.create_invoice(
            asset="USDT",
            amount=int(amount // RUB_TO_USDT),
            description=f"Пополнение баланса на {amount} руб",
            payload=f"{callback_query.message.chat.id}:{int(amount)}",
        )

        if hasattr(invoice, "bot_invoice_url"):
            # Build keyboard
            kb = build_invoice_kb(amount, invoice.bot_invoice_url)
            # Answer message
            await callback_query.message.answer(
                text=f"Вы выбрали пополнение на {amount} рублей.",
                reply_markup=kb,
            )
        else:
            await callback_query.message.answer("Ошибка при создании платежа.")
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")


async def cryptobot_webhook(request):
    try:
        data = await request.json()
        logger.info(f"Получены данные вебхука: {data}")
        if data.get("update_type") == "invoice_paid":
            await process_crypto_payment(data["payload"])
            return web.Response(status=200)
        else:
            logger.warning(f"Неподдерживаемый тип обновления: {data.get('update_type')}")
            return web.Response(status=400)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
        return web.Response(status=500)


async def process_crypto_payment(payload):
    if payload["status"] == "paid":
        custom_payload = payload["payload"]
        user_id_str, amount_str = custom_payload.split(":")
        try:
            user_id = int(user_id_str)
            amount = int(amount_str)
            await add_payment(int(user_id), float(amount), "cryptobot")
            logger.debug(f"Payment succeeded for user_id: {user_id}, amount: {amount}")
            await update_balance(user_id, amount)
            await send_payment_success_notification(user_id, amount)
        except ValueError as e:
            logger.error(f"Ошибка конвертации user_id или amount: {e}")
    else:
        logger.warning(f"Получен неоплаченный инвойс: {payload}")


@router.callback_query(F.data == "enter_custom_amount_cryptobot")
async def process_enter_custom_amount(callback_query: types.CallbackQuery, state: FSMContext):
    # Build keyboard
    kb = build_back_kb("pay_cryptobot", "🔙 Назад")

    # Answer message
    await callback_query.message.answer(
        "Пожалуйста, введите сумму пополнения.",
        reply_markup=kb,
    )

    # Set state
    await state.set_state(ReplenishBalanceState.entering_custom_amount_crypto)


@router.message(ReplenishBalanceState.entering_custom_amount_crypto)
async def process_custom_amount_input(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        amount = int(message.text)
        if amount // RUB_TO_USDT <= 0:
            await message.answer(f"Сумма должна быть больше {RUB_TO_USDT}. Пожалуйста, введите сумму еще раз:")
            return

        await state.update_data(amount=amount)
        await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_crypto)
        try:
            invoice = await crypto.create_invoice(
                asset="USDT",
                amount=int(amount // RUB_TO_USDT),
                description=f"Пополнение баланса на {amount} руб",
                payload=f"{message.chat.id}:{amount}",
            )

            if hasattr(invoice, "bot_invoice_url"):
                # Build keyboard
                kb = build_pay_url_kb(invoice.bot_invoice_url)
                # Answer message
                await message.answer(
                    text=f"Вы выбрали пополнение на {amount} рублей.",
                    reply_markup=kb,
                )
        except Exception as e:
            logger.error(f"Ошибка при создании платежа: {e}")
    else:
        await message.answer("Некорректная сумма. Пожалуйста, введите сумму еще раз:")
