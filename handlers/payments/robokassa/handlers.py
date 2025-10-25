from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from database import add_user, check_user_exists, get_key_count, get_temporary_data
from handlers.buttons import CUSTOM_AMOUNT, PAY_2, MAIN_MENU
from handlers.payments.keyboards import (
    back_keyboard,
    build_amounts_keyboard,
    parse_amount_from_callback,
    pay_keyboard as build_pay_keyboard,
    payment_options_for_user,
)

from handlers.texts import DEFAULT_PAYMENT_MESSAGE, ENTER_SUM
from handlers.payments.currency_rates import format_for_user
from handlers.utils import edit_or_send_message
from logger import logger

from .service import create_and_store_robokassa_payment


router = Router()


class ReplenishBalanceState(StatesGroup):
    choosing_amount_robokassa = State()
    waiting_for_payment_confirmation_robokassa = State()


@router.callback_query(F.data == "pay_robokassa")
async def process_callback_pay_robokassa(callback_query: types.CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.message.chat.id
    b = await get_key_count(session, tg_id)
    if b == 0 and not await check_user_exists(session, tg_id):
        u = callback_query.from_user
        await add_user(
            tg_id=u.id,
            username=u.username,
            first_name=u.first_name,
            last_name=u.last_name,
            language_code=u.language_code,
            is_bot=u.is_bot,
            session=session,
        )
        logger.info(f"[DB] Новый пользователь {tg_id} создан через Robokassa.")

    language_code = getattr(callback_query.from_user, "language_code", None)
    opts = await payment_options_for_user(session, tg_id, language_code, force_currency="RUB")

    markup = build_amounts_keyboard(
        prefix="robokassa",
        pattern="{prefix}_amount|{price}",
        back_cb="balance",
        custom_cb=(CUSTOM_AMOUNT, "enter_custom_amount_robokassa"),
        per_row=2,
        opts=opts,
    )

    await callback_query.message.delete()
    m = await callback_query.message.answer(text="Выберите сумму пополнения:", reply_markup=markup)
    await state.update_data(message_id=m.message_id, chat_id=m.chat.id)
    await state.set_state(ReplenishBalanceState.choosing_amount_robokassa)


@router.callback_query(F.data.startswith("robokassa_"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    amount = parse_amount_from_callback(callback_query.data, prefixes=["robokassa"])
    if not amount or amount <= 0:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    tg_id = callback_query.message.chat.id
    url, _ = await create_and_store_robokassa_payment(session, tg_id, amount, "Пополнение баланса", inv_id=0)

    kb = build_pay_keyboard(
        url,
        pay_text=PAY_2,
        back_cb="pay_robokassa",
    )

    language_code = getattr(callback_query.from_user, "language_code", None)
    amount_text = await format_for_user(session, tg_id, float(amount), language_code, force_currency="RUB")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=DEFAULT_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=kb,
        force_text=True,
    )


@router.callback_query(F.data == "enter_custom_amount_robokassa")
async def process_custom_amount_selection(callback_query: types.CallbackQuery, state: FSMContext):
    b = back_keyboard("pay_robokassa")
    await edit_or_send_message(target_message=callback_query.message, text=ENTER_SUM, reply_markup=b, force_text=True)
    await state.set_state(ReplenishBalanceState.waiting_for_payment_confirmation_robokassa)


async def handle_custom_amount_input(
    event: types.Message | types.CallbackQuery,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    if isinstance(event, types.CallbackQuery):
        message = event.message
        from_user = event.from_user
        tg_id = from_user.id
        temp_data = await get_temporary_data(session, tg_id)
        if not temp_data or temp_data["state"] not in ["waiting_for_payment", "waiting_for_renewal_payment", "waiting_for_gift_payment"]:
            await edit_or_send_message(target_message=message, text="❌ Не удалось получить данные для оплаты.")
            return
        amount = int(temp_data["data"].get("required_amount", 0))
        if amount <= 0:
            await edit_or_send_message(target_message=message, text="❌ Не удалось определить сумму оплаты.")
            return
    else:
        message = event
        from_user = message.from_user
        tg_id = from_user.id
        text = message.text
        if not text or not text.isdigit():
            await message.answer("Введите корректную сумму числом.")
            return
        amount = int(text)
        if amount <= 0:
            await message.answer("Сумма должна быть больше нуля.")
            return

    try:
        url, _ = await create_and_store_robokassa_payment(session, tg_id, amount, "Пополнение баланса", inv_id=0)

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=pay_button_text, url=url)],
                [InlineKeyboardButton(text=main_menu_text, callback_data="profile")],
            ]
        )

        language_code = getattr(from_user, "language_code", None)
        amount_text = await format_for_user(session, tg_id, float(amount), language_code, force_currency="RUB")
        text_out = DEFAULT_PAYMENT_MESSAGE.format(amount=amount_text)

        await edit_or_send_message(target_message=message, text=text_out, reply_markup=markup)
    except Exception as e:
        from logger import logger as _lg
        _lg.error(f"Ошибка при создании платежа для пользователя {tg_id}: {e}")
        await edit_or_send_message(
            target_message=message,
            text="Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
