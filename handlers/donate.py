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
from sqlalchemy.ext.asyncio import AsyncSession

from config import RUB_TO_XTR
from handlers.localization import get_user_texts, get_user_buttons
from logger import logger

from .utils import edit_or_send_message


class DonateState(StatesGroup):
    entering_donate_amount = State()
    waiting_for_donate_confirmation = State()
    waiting_for_donate_payment = State()


router = Router()


@router.callback_query(F.data == "donate")
async def process_donate(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()

    # Получаем локализованные тексты и кнопки
    user_id = callback_query.from_user.id
    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=texts.DONATE_STARS_BOT_BUTTON, url="https://t.me/PremiumBot"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=texts.DONATE_ENTER_AMOUNT_BUTTON,
            callback_data="enter_custom_donate_amount",
        )
    )
    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=texts.DONATE_SUPPORT_TEXT,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "enter_custom_donate_amount")
async def process_enter_donate_amount(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    # Получаем локализованные тексты и кнопки
    user_id = callback_query.from_user.id
    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data="donate"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=texts.DONATE_ENTER_AMOUNT,
        reply_markup=builder.as_markup(),
    )

    await state.set_state(DonateState.entering_donate_amount)


@router.message(DonateState.entering_donate_amount)
async def process_donate_amount_input(message: Message, state: FSMContext, session: AsyncSession):
    # Получаем локализованные тексты и кнопки
    user_id = message.from_user.id
    texts = await get_user_texts(session, user_id)
    buttons = await get_user_buttons(session, user_id)

    if message.text.isdigit():
        amount = int(message.text)
        if amount // RUB_TO_XTR <= 0:
            await message.answer(
                texts.DONATE_AMOUNT_TOO_SMALL.format(min_amount=RUB_TO_XTR)
            )
            return

        await state.update_data(amount=amount)
        await state.set_state(DonateState.waiting_for_donate_confirmation)

        try:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=texts.DONATE_PAY_BUTTON, pay=True))
            builder.row(InlineKeyboardButton(text=buttons.BACK, callback_data="donate"))

            await message.answer_invoice(
                title=texts.DONATE_INVOICE_TITLE.format(amount=amount),
                description=texts.DONATE_INVOICE_DESCRIPTION,
                prices=[LabeledPrice(label=texts.DONATE_LABEL, amount=int(amount // RUB_TO_XTR))],
                provider_token="",
                payload=f"{amount}_donate",
                currency="XTR",
                reply_markup=builder.as_markup(),
            )
            await state.set_state(DonateState.waiting_for_donate_payment)
        except Exception as e:
            logger.error(f"Ошибка при создании доната: {e}")
            await message.answer(texts.DONATE_ERROR_CREATING.format(error=str(e)))
    else:
        await message.answer(texts.DONATE_INVALID_AMOUNT)


@router.pre_checkout_query(DonateState.waiting_for_donate_payment)
async def on_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment, DonateState.waiting_for_donate_payment)
async def on_successful_donate(message: Message, state: FSMContext, session: AsyncSession):
    try:
        # Получаем локализованные тексты и кнопки
        user_id = message.from_user.id
        texts = await get_user_texts(session, user_id)
        buttons = await get_user_buttons(session, user_id)

        amount = float(message.successful_payment.invoice_payload.split("_")[0])
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))
        await message.answer(
            text=texts.DONATE_SUCCESS_MESSAGE.format(amount=amount),
            reply_markup=builder.as_markup(),
        )
        await state.clear()
    except ValueError as e:
        logger.error(f"Ошибка конвертации user_id или amount: {e}")
    except Exception as e:
        logger.error(f"Произошла ошибка при обработке доната: {e}")
