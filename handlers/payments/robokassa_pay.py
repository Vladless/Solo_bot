import hashlib
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web
from robokassa import HashAlgorithm, Robokassa

from config import ROBOKASSA_ENABLE, ROBOKASSA_LOGIN, ROBOKASSA_PASSWORD1, ROBOKASSA_PASSWORD2, ROBOKASSA_TEST_MODE
from database import add_connection, add_payment, check_connection_exists, get_key_count, update_balance
from handlers.payments.utils import send_payment_success_notification
from keyboards.common_kb import build_back_kb
from keyboards.payments.pay_common_kb import build_payment_kb, build_pay_url_kb, build_invoice_kb
from logger import logger

router = Router()


class ReplenishBalanceState(StatesGroup):
    choosing_amount_robokassa = State()
    waiting_for_payment_confirmation_robokassa = State()


if ROBOKASSA_ENABLE:
    robokassa = Robokassa(
        merchant_login=ROBOKASSA_LOGIN,
        password1=ROBOKASSA_PASSWORD1,
        password2=ROBOKASSA_PASSWORD2,
        algorithm=HashAlgorithm.md5,
        is_test=ROBOKASSA_TEST_MODE,
    )

    logger.info("Robokassa initialized with login: {}", ROBOKASSA_LOGIN)


@router.callback_query(F.data == "pay_robokassa")
async def process_callback_pay_robokassa(callback_query: types.CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} initiated Robokassa payment.")

    # Check keys count
    key_count = await get_key_count(tg_id)
    if key_count == 0:
        exists = await check_connection_exists(tg_id)
        if not exists:
            await add_connection(tg_id, balance=0.0, trial=0, session=session)
            logger.info(f"Created new connection for user {tg_id} with balance 0.0.")

    # Build keyboard
    kb = build_payment_kb("robokassa")

    # Answer message
    await callback_query.message.answer(
        text="Выберите сумму пополнения:",
        reply_markup=kb,
    )

    # Set state
    await state.set_state(ReplenishBalanceState.choosing_amount_robokassa)
    logger.info(f"Displayed amount selection for user {tg_id}.")


@router.callback_query(F.data.startswith("robokassa_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    logger.info(f"Получены данные callback_data: {callback_query.data}")

    data = callback_query.data.split("|")
    if len(data) != 3 or data[1] != "amount":
        logger.error("Ошибка: callback_data не соответствует формату.")
        await callback_query.message.answer("Ошибка: данные повреждены.")
        return

    amount_str = data[2]
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError("Сумма должна быть положительным числом.")
    except ValueError as e:
        logger.error(f"Некорректное значение суммы: {amount_str}. Ошибка: {e}")
        await callback_query.message.answer("Некорректная сумма.")
        return

    await state.update_data(amount=amount)
    logger.info(f"User {callback_query.message.chat.id} selected amount: {amount}.")
    inv_id = 0

    tg_id = callback_query.message.chat.id
    payment_url = generate_payment_link(amount, inv_id, "Пополнение баланса", tg_id)

    logger.info(f"Payment URL for user {callback_query.message.chat.id}: {payment_url}")

    # Build keyboard
    kb = build_invoice_kb(amount, payment_url)

    # Answer message
    await callback_query.message.answer(
        text=f"Вы выбрали пополнение на {amount} рублей. Для оплаты перейдите по ссылке ниже:",
        reply_markup=kb,
    )
    logger.info(f"Payment link sent to user {callback_query.message.chat.id}.")


@router.callback_query(F.data == "enter_custom_amount_robokassa")
async def process_custom_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} chose to enter a custom amount.")

    # Build keyboard
    kb = build_back_kb("pay_robokassa")

    # Answer message
    await callback_query.message.answer(
        text="Пожалуйста, введите сумму пополнения.",
        reply_markup=kb,
    )

    await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_robokassa)


@router.message(ReplenishBalanceState.waiting_for_payment_confirmation_robokassa)
async def handle_custom_amount_input(message: types.Message, state: FSMContext):
    tg_id = message.chat.id
    logger.info(f"User {tg_id} entered custom amount: {message.text}")
    inv_id = 0

    try:
        amount = int(message.text)
        if amount <= 0:
            raise ValueError("Сумма должна быть положительным числом.")

        await state.update_data(amount=amount)

        payment_url = generate_payment_link(amount, inv_id, "Пополнение баланса", tg_id)

        logger.info(f"Generated payment link for user {tg_id}: {payment_url}")

        # Build keyboard
        kb = build_pay_url_kb(payment_url)

        # Answer message
        await message.answer(
            text=f"Вы выбрали пополнение на {amount} рублей. Для оплаты перейдите по ссылке ниже:",
            reply_markup=kb,
        )
        await state.clear()
    except ValueError as e:
        logger.error(f"Некорректная сумма от пользователя {tg_id}: {e}")
        await message.answer(text="Введите корректную сумму в рублях (целое положительное число).")


async def robokassa_webhook(request):
    """Обработка webhook-уведомлений от Robokassa с учетом shp_id."""
    try:
        params = await request.post()

        logger.info(f"Received webhook params: {params}")

        amount = params.get("OutSum")
        inv_id = params.get("InvId")
        shp_id = params.get("shp_id")
        signature_value = params.get("SignatureValue")

        logger.info(f"OutSum: {amount}, InvId: {inv_id}, shp_id: {shp_id}, SignatureValue: {signature_value}")

        if not check_payment_signature(params):
            logger.error("Неверная подпись или данные запроса.")
            return web.Response(status=400)

        if not amount or not inv_id or not shp_id:
            logger.error("Отсутствуют обязательные параметры.")
            return web.Response(status=400)

        tg_id = shp_id

        logger.info(f"Processing payment for user {tg_id} with amount {amount}.")

        await update_balance(int(tg_id), float(amount))
        await send_payment_success_notification(tg_id, float(amount))

        await add_payment(int(tg_id), float(amount), "robokassa")

        logger.info(f"Payment successful. Balance updated for user {tg_id}.")

        return web.Response(text=f"OK{inv_id}")

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return web.Response(status=500)


def check_payment_signature(params):
    """Проверка подписи запроса от Robokassa с учетом shp_id."""
    out_sum = params.get("OutSum")
    inv_id = params.get("InvId")
    signature_value = params.get("SignatureValue")
    shp_id = params.get("shp_id")

    signature_string = f"{out_sum}:{inv_id}:{ROBOKASSA_PASSWORD2}:shp_id={shp_id}"

    logger.info(f"Signature string before hashing: {signature_string}")

    expected_signature = hashlib.md5(signature_string.encode("utf-8")).hexdigest().upper()

    logger.info(f"Expected signature: {expected_signature}")
    logger.info(f"Received signature: {signature_value}")

    return signature_value.upper() == expected_signature.upper()


def generate_payment_link(amount, inv_id, description, tg_id):
    """Генерация ссылки на оплату."""
    logger.debug(f"Generating payment link for amount: {amount}, inv_id: {inv_id}, description: {description}")
    payment_link = robokassa._payment.link.generate_by_script(
        out_sum=amount,
        inv_id=inv_id,
        description="пополнение баланса",
        id=f"{tg_id}",
    )
    logger.info(f"Generated payment link: {payment_link}")
    return payment_link
