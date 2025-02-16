import hashlib
from typing import Any

import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from robokassa import HashAlgorithm, Robokassa

from config import (
    DATABASE_URL,
    ROBOKASSA_ENABLE,
    ROBOKASSA_LOGIN,
    ROBOKASSA_PASSWORD1,
    ROBOKASSA_PASSWORD2,
    ROBOKASSA_TEST_MODE,
)
from database import (
    add_connection,
    add_payment,
    check_connection_exists,
    get_key_count,
    get_temporary_data,
    update_balance,
)
from handlers.payments.utils import send_payment_success_notification
from handlers.texts import PAYMENT_OPTIONS
from logger import logger

from handlers.utils import edit_or_send_message

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


def generate_payment_link(amount, inv_id, description, tg_id):
    """Генерация ссылки на оплату."""
    logger.debug(
        f"Generating payment link for amount: {amount}, inv_id: {inv_id}, description: {description}"
    )
    payment_link = robokassa._payment.link.generate_by_script(
        out_sum=amount,
        inv_id=inv_id,
        description="пополнение баланса",
        id=f"{tg_id}",
    )
    logger.info(f"Generated payment link: {payment_link}")
    return payment_link


@router.callback_query(F.data == "pay_robokassa")
async def process_callback_pay_robokassa(
    callback_query: types.CallbackQuery, state: FSMContext, session: Any
):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} initiated Robokassa payment.")

    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'robokassa_amount|{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'robokassa_amount|{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'robokassa_amount|{PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"))

    key_count = await get_key_count(tg_id)

    if key_count == 0:
        exists = await check_connection_exists(tg_id)
        if not exists:
            await add_connection(tg_id, balance=0.0, trial=0, session=session)
            logger.info(f"Created new connection for user {tg_id} with balance 0.0.")

    await edit_or_send_message(
        target_message=callback_query.message,
        text="Выберите сумму пополнения:",
        reply_markup=builder.as_markup(),
        force_text=True
    )
    await state.set_state(ReplenishBalanceState.choosing_amount_robokassa)
    logger.info(f"Displayed amount selection for user {tg_id}.")


@router.callback_query(F.data.startswith("robokassa_amount|"))
async def process_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext
):
    logger.info(f"Получены данные callback_data: {callback_query.data}")

    data = callback_query.data.split("|")
    if len(data) != 3 or data[1] != "amount":
        logger.error("Ошибка: callback_data не соответствует формату.")
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: данные повреждены.",
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True
        )
        return

    amount_str = data[2]
    try:
        amount = int(amount_str)
        if amount <= 0:
            raise ValueError("Сумма должна быть положительным числом.")
    except ValueError as e:
        logger.error(f"Некорректное значение суммы: {amount_str}. Ошибка: {e}")
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True
        )
        return

    await state.update_data(amount=amount)
    logger.info(f"User {callback_query.message.chat.id} selected amount: {amount}.")
    inv_id = 0

    tg_id = callback_query.message.chat.id
    payment_url = generate_payment_link(amount, inv_id, "Пополнение баланса", tg_id)

    logger.info(f"Payment URL for user {callback_query.message.chat.id}: {payment_url}")

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить", url=payment_url)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="pay_robokassa")],
        ]
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"Вы выбрали пополнение на {amount} рублей. Для оплаты перейдите по ссылке ниже:",
        reply_markup=confirm_keyboard,
        force_text=True
    )
    logger.info(f"Payment link sent to user {callback_query.message.chat.id}.")


async def robokassa_webhook(request):
    """Обработка webhook-уведомлений от Robokassa с учетом shp_id."""
    try:
        params = await request.post()

        logger.info(f"Received webhook params: {params}")

        amount = params.get("OutSum")
        inv_id = params.get("InvId")
        shp_id = params.get("shp_id")
        signature_value = params.get("SignatureValue")

        logger.info(
            f"OutSum: {amount}, InvId: {inv_id}, shp_id: {shp_id}, SignatureValue: {signature_value}"
        )

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

    expected_signature = (
        hashlib.md5(signature_string.encode("utf-8")).hexdigest().upper()
    )

    logger.info(f"Expected signature: {expected_signature}")
    logger.info(f"Received signature: {signature_value}")

    return signature_value.upper() == expected_signature.upper()


@router.callback_query(F.data == "enter_custom_amount_robokassa")
async def process_custom_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext
):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} chose to enter a custom amount.")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="pay_robokassa"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text="Пожалуйста, введите сумму пополнения.",
        reply_markup=builder.as_markup(),
        force_text=True
    )

    await state.set_state(
        ReplenishBalanceState.waiting_for_payment_confirmation_robokassa
    )


@router.message(ReplenishBalanceState.waiting_for_payment_confirmation_robokassa)
async def handle_custom_amount_input(message: types.Message | types.CallbackQuery, state: FSMContext = None, session: Any = None):
    if isinstance(message, types.CallbackQuery):
        tg_id = message.message.chat.id
        target_message = message.message
    else:
        tg_id = message.chat.id
        target_message = message

    logger.info(f"User {tg_id} initiated payment through ROBOKASSA")
    inv_id = 0

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        user_data = await get_temporary_data(conn, tg_id)
        await conn.close()

        if not user_data:
            await edit_or_send_message(
                target_message=target_message,
                text="Данные для оплаты не найдены. Попробуйте снова.",
                reply_markup=types.InlineKeyboardMarkup()
            )
            return

        state_type = user_data["state"]
        amount = user_data["data"].get("required_amount", 0)

        if amount <= 0:
            await edit_or_send_message(
                target_message=target_message,
                text="Недостаточная сумма для пополнения.",
                reply_markup=types.InlineKeyboardMarkup()
            )
            return

        payment_url = generate_payment_link(amount, inv_id, "Пополнение баланса", tg_id)
        logger.info(f"Generated payment link for user {tg_id}: {payment_url}")

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="💳 Оплатить", url=payment_url))
        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay_robokassa"))

        if state_type == "waiting_for_payment":
            message_text = f"Вы выбрали пополнение на {amount} рублей для создания нового ключа. Перейдите по ссылке для оплаты:"
        elif state_type == "waiting_for_renewal_payment":
            message_text = f"Вы выбрали пополнение на {amount} рублей для продления ключа. Перейдите по ссылке для оплаты:"
        else:
            await edit_or_send_message(
                target_message=target_message,
                text="Некорректное состояние данных. Попробуйте снова.",
                reply_markup=types.InlineKeyboardMarkup()
            )
            return

        await edit_or_send_message(
            target_message=target_message,
            text=message_text,
            reply_markup=builder.as_markup()
        )

        if isinstance(state, FSMContext):
            await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при создании платежа для пользователя {tg_id}: {e}")
        await edit_or_send_message(
            target_message=target_message,
            text="Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=types.InlineKeyboardMarkup()
        )
