from typing import Any

from aiocryptopay import AioCryptoPay, Networks
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web

from config import CRYPTO_BOT_ENABLE, CRYPTO_BOT_TOKEN, RUB_TO_USDT
from database import add_connection, add_payment, check_connection_exists, get_key_count, update_balance
from routers.handlers.payments.utils import send_payment_success_notification
from routers.handlers import PAYMENT_OPTIONS
from logger import logger

router = Router(name=__name__)

if CRYPTO_BOT_ENABLE:
    crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)


class ReplenishBalanceState(StatesGroup):
    choosing_amount_crypto = State()
    waiting_for_payment_confirmation_crypto = State()
    entering_custom_amount_crypto = State()


@router.callback_query(F.data == "pay_cryptobot")
async def process_callback_pay_cryptobot(callback_query: types.CallbackQuery, state: FSMContext, session: Any):
    builder = InlineKeyboardBuilder()
    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'crypto_{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'crypto_{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'crypto_{PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="💰 Ввести свою сумму",
            callback_data="enter_custom_amount_crypto",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"))
    key_count = await get_key_count(callback_query.message.chat.id)
    if key_count == 0:
        exists = await check_connection_exists(callback_query.message.chat.id)
        if not exists:
            await add_connection(tg_id=callback_query.message.chat.id, balance=0.0, trial=0, session=session)
    await callback_query.message.answer(
        "Выберите сумму пополнения:",
        reply_markup=builder.as_markup(),
    )
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
            amount=str(int(amount // RUB_TO_USDT)),
            description=f"Пополнения баланса на {amount} руб",
            payload=f"{callback_query.message.chat.id}:{int(amount)}",
        )

        if hasattr(invoice, "bot_invoice_url"):
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="Пополнить", url=invoice.bot_invoice_url))
            builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"))
            await callback_query.message.answer(
                text=f"Вы выбрали пополнение на {amount} рублей.",
                reply_markup=builder.as_markup(),
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


@router.callback_query(F.data == "enter_custom_amount_crypto")
async def process_enter_custom_amount(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.answer(text="Введите сумму пополнения:")
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
                amount=str(int(amount // RUB_TO_USDT)),
                description=f"Пополнения баланса на {amount} руб",
                payload=f"{message.chat.id}:{amount}",
            )

            if hasattr(invoice, "bot_invoice_url"):
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text="Пополнить", url=invoice.bot_invoice_url))
                builder.row(
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"),
                )
                await message.answer(
                    text=f"Вы выбрали пополнение на {amount} рублей.",
                    reply_markup=builder.as_markup(),
                )
        except Exception as e:
            logger.error(f"Ошибка при создании платежа: {e}")
    else:
        await message.answer("Некорректная сумма. Пожалуйста, введите сумму еще раз:")
