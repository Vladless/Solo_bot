import hashlib
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from robokassa import HashAlgorithm, Robokassa
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
from sqlalchemy import select, and_

from config import (
    ROBOKASSA_ENABLE,
    ROBOKASSA_LOGIN,
    ROBOKASSA_PASSWORD1,
    ROBOKASSA_PASSWORD2,
    ROBOKASSA_TEST_MODE,
)
from database import (
    add_payment,
    add_user,
    async_session_maker,
    check_user_exists,
    get_key_count,
    get_temporary_data,
    update_balance,
    Payment
)
from handlers.localization import get_user_texts, get_user_buttons
from handlers.payments.utils import send_payment_success_notification
from handlers.utils import edit_or_send_message
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
    callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession
):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} initiated Robokassa payment.")

    # Получаем локализованные тексты и кнопки для пользователя
    texts = await get_user_texts(session, tg_id)
    buttons = await get_user_buttons(session, tg_id)

    builder = InlineKeyboardBuilder()
    for i in range(0, len(texts.PAYMENT_OPTIONS), 2):
        if i + 1 < len(texts.PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=texts.PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'robokassa_amount|{texts.PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=texts.PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'robokassa_amount|{texts.PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=texts.PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'robokassa_amount|{texts.PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )
    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data="balance"))

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
            logger.info(f"[DB] Новый пользователь {tg_id} создан через Robokassa.")

    await callback_query.message.delete()

    new_message = await callback_query.message.answer(
        text=texts.CHOOSE_PAYMENT_AMOUNT,
        reply_markup=builder.as_markup(),
    )
    await state.update_data(
        message_id=new_message.message_id, chat_id=new_message.chat.id
    )
    await state.set_state(ReplenishBalanceState.choosing_amount_robokassa)
    logger.info(f"Displayed amount selection for user {tg_id}.")


@router.callback_query(F.data.startswith("robokassa_amount|"))
async def process_amount_selection(
    callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession
):
    logger.info(f"Получены данные callback_data: {callback_query.data}")

    tg_id = callback_query.message.chat.id
    
    # Получаем локализованные тексты и кнопки для пользователя
    texts = await get_user_texts(session, tg_id)
    buttons = await get_user_buttons(session, tg_id)

    data = callback_query.data.split("|")
    if len(data) != 3 or data[1] != "amount":
        logger.error("Ошибка: callback_data не соответствует формату.")
        await edit_or_send_message(
            target_message=callback_query.message,
            text=texts.PAYMENT_DATA_CORRUPTED,
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
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
            text=texts.INVALID_AMOUNT,
            reply_markup=types.InlineKeyboardMarkup(),
            force_text=True,
        )
        return

    await state.update_data(amount=amount)
    logger.info(f"User {callback_query.message.chat.id} selected amount: {amount}.")
    inv_id = 0

    payment_url = generate_payment_link(amount, inv_id, texts.BALANCE_REPLENISHMENT, tg_id)

    logger.info(f"Payment URL for user {callback_query.message.chat.id}: {payment_url}")

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=buttons.PAYMENT_BUTTON_TEXT, url=payment_url)],
            [InlineKeyboardButton(text=buttons.BACK, callback_data="pay_robokassa")],
        ]
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=texts.DEFAULT_PAYMENT_MESSAGE.format(amount=amount),
        reply_markup=confirm_keyboard,
        force_text=True,
    )
    logger.info(f"Payment link sent to user {callback_query.message.chat.id}.")


async def robokassa_webhook(request: web.Request):
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

        async with async_session_maker() as session:
            recent_time = datetime.utcnow() - timedelta(minutes=1)

            result = await session.execute(
                select(Payment).where(
                    and_(
                        Payment.tg_id == int(tg_id),
                        Payment.amount == float(amount),
                        Payment.status == "success",
                        Payment.created_at >= recent_time
                    )
                )
            )
            duplicate = result.scalar_one_or_none()

            if duplicate:
                logger.warning(f"[Robokassa] Повторный webhook. Платёж уже обработан: tg_id={tg_id}, amount={amount}")
                return web.Response(text=f"OK{inv_id}")

            await update_balance(session, int(tg_id), float(amount))
            await send_payment_success_notification(tg_id, float(amount), session)
            await add_payment(session, int(tg_id), float(amount), "robokassa")

        logger.info(f"✅ Payment successful. Balance updated for user {tg_id}.")
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
    callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession
):
    tg_id = callback_query.message.chat.id
    logger.info(f"User {tg_id} chose to enter a custom amount.")

    # Получаем локализованные тексты и кнопки для пользователя
    texts = await get_user_texts(session, tg_id)
    buttons = await get_user_buttons(session, tg_id)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data="pay_robokassa"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=texts.ENTER_SUM,
        reply_markup=builder.as_markup(),
        force_text=True,
    )

    await state.set_state(
        ReplenishBalanceState.waiting_for_payment_confirmation_robokassa
    )


@router.message(ReplenishBalanceState.waiting_for_payment_confirmation_robokassa)
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

    logger.info(f"User {tg_id} initiated payment through ROBOKASSA")
    inv_id = 0

    # Получаем локализованные тексты и кнопки для пользователя
    texts = await get_user_texts(session, tg_id)
    buttons = await get_user_buttons(session, tg_id)

    try:
        user_data = await get_temporary_data(session, tg_id)

        if not user_data:
            await edit_or_send_message(
                target_message=target_message,
                text=texts.PAYMENT_DATA_NOT_FOUND,
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[]),
            )
            return

        state_type = user_data["state"]
        amount = user_data["data"].get("required_amount", 0)

        if amount <= 0:
            await edit_or_send_message(
                target_message=target_message,
                text=texts.INSUFFICIENT_AMOUNT,
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[]),
            )
            return

        payment_url = generate_payment_link(amount, inv_id, texts.BALANCE_REPLENISHMENT, tg_id)
        logger.info(f"Generated payment link for user {tg_id}: {payment_url}")

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=buttons.PAYMENT_BUTTON_TEXT, url=payment_url))
        builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data="pay_robokassa"))

        if state_type == "waiting_for_payment":
            message_text = texts.PAYMENT_FOR_NEW_KEY.format(amount=amount)
        elif state_type == "waiting_for_renewal_payment":
            message_text = texts.PAYMENT_FOR_KEY_RENEWAL.format(amount=amount)
        else:
            await edit_or_send_message(
                target_message=target_message,
                text=texts.INVALID_STATE_DATA,
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[]),
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
            text=texts.PAYMENT_CREATION_ERROR,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[]),
        )
