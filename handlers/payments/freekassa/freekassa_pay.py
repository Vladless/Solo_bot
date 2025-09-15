import hashlib

from datetime import datetime, timedelta
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    FREEKASSA_SECRET1,
    FREEKASSA_SECRET2,
    FREEKASSA_SHOP_ID,
)
from database import (
    add_payment,
    add_user,
    async_session_maker,
    check_user_exists,
    get_key_count,
    get_temporary_data,
    update_balance,
)
from database.models import Payment
from handlers.buttons import BACK, PAY_2
from handlers.payments.utils import send_payment_success_notification
from handlers.texts import DEFAULT_PAYMENT_MESSAGE, ENTER_SUM, PAYMENT_OPTIONS
from handlers.utils import edit_or_send_message
from logger import logger


router = Router()


class ReplenishBalanceState(StatesGroup):
    choosing_amount_freekassa = State()
    waiting_for_payment_confirmation_freekassa = State()


def generate_signature(shop_id: int, amount: float, secret: str, order_id: str, currency: str = "RUB") -> str:
    signature_string = f"{shop_id}:{amount}:{secret}:{currency}:{order_id}"
    signature = hashlib.md5(signature_string.encode("utf-8")).hexdigest()
    logger.debug(f"Generated signature for order {order_id}: {signature}")
    return signature


def generate_payment_link(amount: float, order_id: str, tg_id: int, currency: str = "RUB") -> str:
    signature = generate_signature(FREEKASSA_SHOP_ID, amount, FREEKASSA_SECRET1, order_id, currency)

    payment_url = "https://pay.fk.money/"
    params = {
        "m": FREEKASSA_SHOP_ID,
        "oa": amount,
        "currency": currency,
        "o": order_id,
        "s": signature,
        "us_tg_id": tg_id,
    }

    query_string = "&".join([f"{key}={value}" for key, value in params.items()])
    full_url = f"{payment_url}?{query_string}"

    logger.info(f"Generated Freekassa payment link: {full_url}")
    return full_url


@router.callback_query(F.data == "pay_freekassa")
async def process_callback_pay_freekassa(callback_query: types.CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} initiated Freekassa payment.")

    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f"freekassa_amount|{PAYMENT_OPTIONS[i]['callback_data']}",
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f"freekassa_amount|{PAYMENT_OPTIONS[i + 1]['callback_data']}",
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f"freekassa_amount|{PAYMENT_OPTIONS[i]['callback_data']}",
                )
            )
    builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

    key_count = await get_key_count(session, tg_id)

    if key_count == 0:
        exists = await check_user_exists(session, tg_id)
        if not exists:
            from_user = callback_query.from_user
            await add_user(
                tg_id=from_user.id,
                username=from_user.username,
                first_name=from_user.first_name,
                last_name=from_user.last_name,
                language_code=from_user.language_code,
                is_bot=from_user.is_bot,
                session=session,
            )
            logger.info(f"[DB] Новый пользователь {tg_id} создан через Freekassa.")

    await callback_query.message.delete()

    new_message = await callback_query.message.answer(
        text="Выберите сумму пополнения:",
        reply_markup=builder.as_markup(),
    )
    await state.update_data(message_id=new_message.message_id, chat_id=new_message.chat.id)
    await state.set_state(ReplenishBalanceState.choosing_amount_freekassa)
    logger.info(f"Displayed amount selection for user {tg_id}.")


@router.callback_query(F.data.startswith("freekassa_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    logger.info(f"Получены данные callback_data: {callback_query.data}")

    data = callback_query.data.split("|")
    if len(data) != 3 or data[1] != "amount":
        logger.error("Ошибка: callback_data не соответствует формату.")
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: данные повреждены.",
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
        )
        return

    amount_str = data[2]
    try:
        amount = float(amount_str)
        if amount <= 0:
            raise ValueError("Сумма должна быть положительным числом.")
    except ValueError as e:
        logger.error(f"Некорректное значение суммы: {amount_str}. Ошибка: {e}")
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
        )
        return

    await state.update_data(amount=amount)
    logger.info(f"User {callback_query.message.chat.id} selected amount: {amount}.")

    tg_id = callback_query.message.chat.id
    order_id = f"order_{tg_id}_{int(amount)}_{hash(str(tg_id) + str(amount))}"

    payment_url = generate_payment_link(amount, order_id, tg_id)

    logger.info(f"Payment URL for user {callback_query.message.chat.id}: {payment_url}")

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=PAY_2, url=payment_url)],
            [InlineKeyboardButton(text=BACK, callback_data="pay_freekassa")],
        ]
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=DEFAULT_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    logger.info(f"Payment link sent to user {callback_query.message.chat.id}.")


def verify_signature(params: dict) -> bool:
    try:
        merchant_id = params.get("MERCHANT_ID", "")
        amount = params.get("AMOUNT", "")
        merchant_order_id = params.get("MERCHANT_ORDER_ID", "")
        sign = params.get("SIGN", "")

        signature_string = f"{merchant_id}:{amount}:{FREEKASSA_SECRET2}:{merchant_order_id}"
        expected_signature = hashlib.md5(signature_string.encode("utf-8")).hexdigest()

        logger.debug(f"Signature verification: expected={expected_signature}, received={sign}")

        return expected_signature == sign
    except Exception as e:
        logger.error(f"Error verifying signature: {e}")
        return False


async def freekassa_webhook(request: web.Request):
    try:
        params = dict(request.query)
        logger.info(f"Received Freekassa webhook: {params}")

        merchant_id = params.get("MERCHANT_ID")
        amount = params.get("AMOUNT")
        merchant_order_id = params.get("MERCHANT_ORDER_ID")
        sign = params.get("SIGN")
        tg_id = params.get("us_tg_id")

        if not all([merchant_id, amount, merchant_order_id, sign]):
            logger.error("Missing required parameters in webhook")
            return web.Response(status=400, text="Missing required parameters")

        if not verify_signature(params):
            logger.error("Invalid signature in webhook")
            return web.Response(status=400, text="Invalid signature")

        if str(merchant_id) != str(FREEKASSA_SHOP_ID):
            logger.error(f"Invalid merchant_id: {merchant_id}")
            return web.Response(status=400, text="Invalid merchant_id")

        try:
            amount_float = float(amount)
            if tg_id:
                tg_id_int = int(tg_id)
            else:
                order_parts = merchant_order_id.split("_")
                if len(order_parts) >= 3 and order_parts[0] == "order":
                    tg_id_int = int(order_parts[1])
                else:
                    logger.error(f"Cannot extract tg_id from order_id: {merchant_order_id}")
                    return web.Response(status=400, text="Cannot identify user")
        except (ValueError, TypeError) as e:
            logger.error(f"Error parsing parameters: {e}")
            return web.Response(status=400, text="Invalid parameter format")

        async with async_session_maker() as session:
            recent_time = datetime.utcnow() - timedelta(minutes=1)
            result = await session.execute(
                select(Payment).where(
                    and_(
                        Payment.tg_id == tg_id_int,
                        Payment.amount == amount_float,
                        Payment.status == "success",
                        Payment.created_at >= recent_time,
                    )
                )
            )
            duplicate = result.scalar_one_or_none()

            if duplicate:
                logger.warning(
                    f"[Freekassa] Повторный webhook. Платёж уже обработан: tg_id={tg_id_int}, amount={amount_float}"
                )
                return web.Response(text="YES")

            await update_balance(session, tg_id_int, amount_float)
            await send_payment_success_notification(tg_id_int, amount_float, session)
            await add_payment(session, tg_id_int, amount_float, "freekassa")

        logger.info(f"Payment processed successfully. User: {tg_id_int}, Amount: {amount_float}")
        return web.Response(text="YES")

    except Exception as e:
        logger.error(f"Error processing Freekassa webhook: {e}")
        return web.Response(status=500, text="Internal server error")


@router.callback_query(F.data == "enter_custom_amount_freekassa")
async def process_custom_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} chose to enter a custom amount.")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_freekassa"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=ENTER_SUM,
        reply_markup=builder.as_markup(),
        force_text=True,
    )

    await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_freekassa)


@router.message(ReplenishBalanceState.waiting_for_payment_confirmation_freekassa)
async def handle_custom_amount_input(
    message: types.Message | types.CallbackQuery,
    state: FSMContext = None,
    session: AsyncSession = None,
):
    if isinstance(message, types.CallbackQuery):
        tg_id = message.message.chat.id
        target_message = message.message
    else:
        tg_id = message.chat.id
        target_message = message

    logger.info(f"User {tg_id} initiated payment through Freekassa")

    try:
        user_data = await get_temporary_data(session, tg_id)

        if not user_data:
            await edit_or_send_message(
                target_message=target_message,
                text="Данные для оплаты не найдены. Попробуйте снова.",
                reply_markup=types.InlineKeyboardMarkup(),
            )
            return

        state_type = user_data["state"]
        amount = user_data["data"].get("required_amount", 0)

        if amount <= 0:
            await edit_or_send_message(
                target_message=target_message,
                text="Недостаточная сумма для пополнения.",
                reply_markup=types.InlineKeyboardMarkup(),
            )
            return

        order_id = f"order_{tg_id}_{int(amount)}_{hash(str(tg_id) + str(amount))}"
        payment_url = generate_payment_link(amount, order_id, tg_id)
        logger.info(f"Generated payment link for user {tg_id}: {payment_url}")

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="💳 Оплатить", url=payment_url))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_freekassa"))

        if state_type == "waiting_for_payment":
            message_text = (
                f"Вы выбрали пополнение на {amount} рублей для создания нового ключа. Перейдите по ссылке для оплаты:"
            )
        elif state_type == "waiting_for_renewal_payment":
            message_text = (
                f"Вы выбрали пополнение на {amount} рублей для продления ключа. Перейдите по ссылке для оплаты:"
            )
        else:
            await edit_or_send_message(
                target_message=target_message,
                text="Некорректное состояние данных. Попробуйте снова.",
                reply_markup=types.InlineKeyboardMarkup(),
            )
            return

        await edit_or_send_message(
            target_message=target_message,
            text=message_text,
            reply_markup=builder.as_markup(),
        )

        if isinstance(state, FSMContext):
            await state.clear()

    except Exception as e:
        logger.error(f"Ошибка при создании платежа для пользователя {tg_id}: {e}")
        await edit_or_send_message(
            target_message=target_message,
            text="Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=types.InlineKeyboardMarkup(),
        )
