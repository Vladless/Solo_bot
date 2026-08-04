from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from logger import logger
from settings.buttons import BACK, MAIN_MENU, STARS_BOT
from settings.config import RUB_TO_XTR, STARS_BOT_URL

from .utils import edit_or_send_message


class DonateState(StatesGroup):
    entering_donate_amount = State()
    waiting_for_donate_confirmation = State()
    waiting_for_donate_payment = State()


router = Router()


@router.callback_query(F.data == "donate")
async def process_donate(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=STARS_BOT, url=STARS_BOT_URL))
    builder.row(
        InlineKeyboardButton(
            text="💰 Ввести сумму доната",
            callback_data="enter_custom_donate_amount",
        )
    )
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    text = (
        "🌟 Поддержите наш проект! 💪\n\n"
        "💖 Каждый донат помогает развивать и улучшать сервис. "
        "🤝 Мы ценим вашу поддержку и работаем над тем, чтобы сделать наш продукт еще лучше. 🚀💡"
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "enter_custom_donate_amount")
async def process_enter_donate_amount(callback_query: CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="donate"))
    text = "💸 Введите сумму доната в рублях:"

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
    )

    await state.set_state(DonateState.entering_donate_amount)


@router.message(DonateState.entering_donate_amount)
async def process_donate_amount_input(message: Message, state: FSMContext):
    if message.text.isdigit():
        amount = int(message.text)
        if amount // RUB_TO_XTR <= 0:
            await message.answer(f"Сумма доната должна быть больше {RUB_TO_XTR}. Пожалуйста, введите сумму еще раз:")
            return

        await state.update_data(amount=amount)
        await state.set_state(DonateState.waiting_for_donate_confirmation)

        try:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="Задонатить", pay=True))
            builder.row(InlineKeyboardButton(text=BACK, callback_data="donate"))

            await message.answer_invoice(
                title=f"Донат проекту {amount} рублей",
                description="Спасибо за вашу поддержку!",
                prices=[LabeledPrice(label="Донат", amount=int(amount // RUB_TO_XTR))],
                provider_token="",
                payload=f"{amount}_donate",
                currency="XTR",
                reply_markup=builder.as_markup(),
            )
            await state.set_state(DonateState.waiting_for_donate_payment)
        except Exception as e:
            logger.error(f"Ошибка при создании доната: {e}")
    else:
        await message.answer("Некорректная сумма. Пожалуйста, введите сумму еще раз:")


@router.pre_checkout_query(DonateState.waiting_for_donate_payment)
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment, DonateState.waiting_for_donate_payment)
async def on_successful_donate(message: Message, state: FSMContext):
    try:
        amount = float(message.successful_payment.invoice_payload.split("_")[0])
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await message.answer(
            text=f"🙏 Спасибо за донат {amount} рублей! Ваша поддержка очень важна для нас. 💖",
            reply_markup=builder.as_markup(),
        )
        await state.clear()
    except ValueError as e:
        logger.error(f"Ошибка конвертации user_id или amount: {e}")
    except Exception as e:
        logger.error(f"Произошла ошибка при обработке доната: {e}")
