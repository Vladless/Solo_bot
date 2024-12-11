import math

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import LabeledPrice, PreCheckoutQuery

from config import RUB_TO_XTR
from keyboards.donate_kb import build_donate_kb, build_donate_back_kb, build_donate_amount_kb
from keyboards.profile_kb import build_profile_back_kb
from logger import logger


class DonateState(StatesGroup):
    entering_donate_amount = State()
    waiting_for_donate_confirmation = State()
    waiting_for_donate_payment = State()


router = Router()


@router.callback_query(F.data == "donate")
async def process_donate(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()

    # Prepare text
    text = (
        "🌟 Поддержите наш проект! 💪\n\n"
        "💖 Каждый донат помогает развивать и улучшать сервис. "
        "🤝 Мы ценим вашу поддержку и работаем над тем, чтобы сделать наш продукт еще лучше. 🚀💡"
    )

    # Build keyboard
    kb = build_donate_kb()

    # Answer message
    await callback_query.message.answer(
        text=text,
        reply_markup=kb,
    )


@router.callback_query(F.data == "enter_custom_donate_amount")
async def process_enter_donate_amount(callback_query: types.CallbackQuery, state: FSMContext):
    # Build keyboard
    kb = build_donate_back_kb()

    # Answer message
    await callback_query.message.answer(
        text="💸 Введите сумму доната в рублях:",
        reply_markup=kb,
    )
    # Set state
    await state.set_state(
        DonateState.entering_donate_amount
    )


@router.message(DonateState.entering_donate_amount)
async def process_donate_amount_input(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        # Answer message
        await message.answer(text="Некорректная сумма. Пожалуйста, введите сумму еще раз:")
        return

    amount = int(message.text)
    if amount // RUB_TO_XTR <= 0:
        # Prepare text
        text = (
            f"Сумма доната должна быть больше {math.ceil(RUB_TO_XTR)}. "
            f"Пожалуйста, введите сумму еще раз:"
        )
        # Answer message
        await message.answer(text=text)
        return

    # Update data and set state
    await state.update_data(amount=amount)
    await state.set_state(DonateState.waiting_for_donate_confirmation)

    try:
        # Build keyboard
        kb = build_donate_amount_kb()

        # Answer message
        await message.answer_invoice(
            title=f"Донат проекту {amount} рублей",
            description="Спасибо за вашу поддержку!",
            prices=[LabeledPrice(label="Донат", amount=int(amount // RUB_TO_XTR))],
            provider_token="",
            payload=f"{amount}_donate",
            currency="XTR",
            reply_markup=kb,
        )
        await state.set_state(DonateState.waiting_for_donate_payment)
    except Exception as e:
        logger.error(f"Ошибка при создании доната: {e}")

        # Build keyboard
        kb = build_donate_back_kb()

        # Answer anyway
        await message.answer(
            text="Ошибка при создании доната",
            reply_markup=kb,
        )


@router.pre_checkout_query(DonateState.waiting_for_donate_payment)
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment, DonateState.waiting_for_donate_payment)
async def on_successful_donate(message: types.Message, state: FSMContext):
    try:
        amount = float(message.successful_payment.invoice_payload.split("_")[0])

        # Build keyboard
        kb = build_profile_back_kb()

        # Answer message
        await message.answer(
            text=f"🙏 Спасибо за донат {amount} рублей! Ваша поддержка очень важна для нас. 💖",
            reply_markup=kb,
        )
        await state.clear()
    except ValueError as e:  # todo: need to answer user anyway
        logger.error(f"Ошибка конвертации user_id или amount: {e}")
    except Exception as e:  # todo: need to answer user anyway
        logger.error(f"Произошла ошибка при обработке доната: {e}")
