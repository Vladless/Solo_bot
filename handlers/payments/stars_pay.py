from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import RUB_TO_XTR
from handlers.texts import PAYMENT_OPTIONS
from logger import logger
from utils.database import add_connection, add_payment, check_connection_exists, get_key_count, update_balance
from utils.payments.utils import send_payment_success_notification

router = Router()


class ReplenishBalanceState(StatesGroup):
    choosing_amount_stars = State()
    waiting_for_payment_confirmation_stars = State()
    entering_custom_amount_stars = State()


@router.callback_query(F.data == "pay_stars")
async def process_callback_pay_stars(callback_query: types.CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.message.chat.id

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🤖 Бот для покупки звезд", url="https://t.me/PremiumBot"))

    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'stars_{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'stars_{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'stars_{PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )
    builder.row(
        InlineKeyboardButton(
            text="💰 Ввести свою сумму",
            callback_data="enter_custom_amount_stars",
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"))

    key_count = await get_key_count(tg_id)

    if key_count == 0:
        exists = await check_connection_exists(tg_id)
        if not exists:
            await add_connection(tg_id, balance=0.0, trial=0, session=session)

    try:
        await callback_query.message.delete()
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    await callback_query.message.answer(
        text="Выберите сумму пополнения:",
        reply_markup=builder.as_markup(),
    )

    await state.set_state(ReplenishBalanceState.choosing_amount_stars)


@router.callback_query(F.data.startswith("stars_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split("|", 1)

    if len(data) != 2:
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        await callback_query.message.answer("Неверные данные для выбора суммы.")
        return

    amount_str = data[1]
    try:
        amount = int(amount_str)
    except ValueError:
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

        await callback_query.message.answer("Некорректная сумма.")
        return

    await state.update_data(amount=amount)
    await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_stars)

    try:

        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(text="Пополнить", pay=True),
        )
        builder.row(
            InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"),
        )

        await callback_query.message.answer_invoice(
            title=f"Вы выбрали пополнение на {amount} рублей.",
            description=f"Вы выбрали пополнение на {amount} рублей.",
            prices=[LabeledPrice(label="XTR", amount=int(amount // RUB_TO_XTR))],
            provider_token="",
            payload=f"{amount}_stars",
            currency="XTR",
            reply_markup=builder.as_markup(),
        )
    except Exception as e:
        logger.error(f"Ошибка при создании платежа: {e}")
        await callback_query.message.answer("Произошла ошибка при создании платежа.")


@router.callback_query(F.data == "enter_custom_amount_stars")
async def process_enter_custom_amount(callback_query: types.CallbackQuery, state: FSMContext):

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="pay_stars"))

    await callback_query.message.answer(
        "Пожалуйста, введите сумму пополнения.",
        reply_markup=builder.as_markup(),
    )

    await state.set_state(ReplenishBalanceState.entering_custom_amount_stars)


@router.message(ReplenishBalanceState.entering_custom_amount_stars)
async def process_custom_amount_input(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        amount = int(message.text)
        if amount // RUB_TO_XTR <= 0:
            await message.answer(f"Сумма должна быть больше {RUB_TO_XTR}. Пожалуйста, введите сумму еще раз:")
            return

        await state.update_data(amount=amount)
        await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_stars)
        try:
            builder = InlineKeyboardBuilder()
            builder.row(
                InlineKeyboardButton(text="Пополнить", pay=True),
            )
            builder.row(
                InlineKeyboardButton(text="⬅️ Назад", callback_data="pay"),
            )
            await message.answer_invoice(
                title=f"Вы выбрали пополнение на {amount} рублей.",
                description=f"Вы выбрали пополнение на {amount} рублей.",
                prices=[LabeledPrice(label="XTR", amount=int(amount // RUB_TO_XTR))],
                provider_token="",
                payload=f"{amount}_stars",
                currency="XTR",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            logger.error(f"Ошибка при создании платежа: {e}")
            await message.answer("Произошла ошибка при создании платежа.")
    else:
        await message.answer("Некорректная сумма. Пожалуйста, введите сумму еще раз:")


@router.pre_checkout_query()
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(
    message: types.Message,
):
    try:
        user_id = int(message.chat.id)
        amount = float(message.successful_payment.invoice_payload.split("_")[0])
        logger.debug(f"Payment succeeded for user_id: {user_id}, amount: {amount}")
        await add_payment(int(user_id), float(amount), "stars")
        await update_balance(user_id, amount)
        await send_payment_success_notification(user_id, amount)
    except ValueError as e:
        logger.error(f"Ошибка конвертации user_id или amount: {e}")
