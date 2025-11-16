from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_temporary_data
from database.models import User
from handlers.buttons import MAIN_MENU, PAY_2
from handlers.payments.currency_rates import format_for_user
from handlers.texts import DEFAULT_PAYMENT_MESSAGE
from handlers.utils import edit_or_send_message
from logger import logger

from .service import (
    KASSAI_PAYMENT_METHODS,
    generate_kassai_payment_link,
    process_callback_pay_kassai,
    router as service_router,
)

router = Router(name="kassai_router")
router.include_router(service_router)


@router.callback_query(F.data == "pay_kassai_cards")
async def handle_pay_kassai_cards(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    """Обработчик оплаты через KassaI картами."""
    await process_callback_pay_kassai(
        callback_query, state, session, method_name="cards"
    )


@router.callback_query(F.data == "pay_kassai_sbp")
async def handle_pay_kassai_sbp(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    """Обработчик оплаты через KassaI СБП."""
    await process_callback_pay_kassai(
        callback_query, state, session, method_name="sbp"
    )


async def _handle_custom_amount_input_kassai(
    event,
    session: AsyncSession,
    method_name: str,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    """Универсальная функция быстрого потока для KassaI.

    Принимает недостающую сумму и формирует платеж.
    Работает с временными данными из fast_payment_flow для
    создания/продления/подарка.

    Args:
        event: Событие от aiogram
        session: Сессия БД
        method_name: "cards" или "sbp"
        pay_button_text: Текст кнопки оплаты
        main_menu_text: Текст кнопки главного меню
    """
    message = event.message
    from_user = event.from_user
    tg_id = from_user.id

    temp_data = await get_temporary_data(session, tg_id)
    valid_states = [
        "waiting_for_payment",
        "waiting_for_renewal_payment",
        "waiting_for_gift_payment",
    ]
    if not temp_data or temp_data["state"] not in valid_states:
        await edit_or_send_message(
            target_message=message,
            text="❌ Не удалось получить данные для оплаты.",
        )
        return

    amount = int(temp_data["data"].get("required_amount", 0))
    if amount <= 0:
        await edit_or_send_message(
            target_message=message,
            text="❌ Не удалось определить сумму оплаты.",
        )
        return

    min_amounts = {"cards": 50, "sbp": 10}
    method_labels = {"cards": "картой", "sbp": "через СБП"}
    min_amount = min_amounts.get(method_name, 10)

    if amount < min_amount:
        method_label = method_labels.get(method_name, "")
        error_msg = (
            f"❌ Минимальная сумма для оплаты {method_label} — "
            f"{min_amount}₽."
        )
        await edit_or_send_message(
            target_message=message,
            text=error_msg,
        )
        return

    method = next(
        (
            m
            for m in KASSAI_PAYMENT_METHODS
            if m["name"] == method_name and m["enable"]
        ),
        None,
    )
    if not method:
        method_label = "картами" if method_name == "cards" else "через СБП"
        await edit_or_send_message(
            target_message=message,
            text=f"❌ Оплата {method_label} KassaAI временно недоступна.",
        )
        return

    try:
        payment_url = await generate_kassai_payment_link(
            amount, tg_id, method
        )

        if not payment_url or payment_url == "https://fk.life/":
            await edit_or_send_message(
                target_message=message,
                text=(
                    "❌ Произошла ошибка при создании платежа. "
                    "Попробуйте позже или выберите другой способ оплаты."
                ),
            )
            return

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=pay_button_text, url=payment_url)],
                [
                    InlineKeyboardButton(
                        text=main_menu_text, callback_data="profile"
                    )
                ],
            ]
        )

        result = await session.execute(
            select(User.language_code).where(User.tg_id == tg_id)
        )
        language_code = result.scalar_one_or_none()
        amount_text = await format_for_user(
            session,
            tg_id,
            float(amount),
            language_code,
            force_currency="RUB",
        )
        text_out = DEFAULT_PAYMENT_MESSAGE.format(amount=amount_text)

        await edit_or_send_message(
            target_message=message, text=text_out, reply_markup=markup
        )
    except Exception as e:
        method_label = "Cards" if method_name == "cards" else "SBP"
        logger.error(
            f"Ошибка при создании платежа KassaAI {method_label} "
            f"для пользователя {tg_id}: {e}"
        )
        await edit_or_send_message(
            target_message=message,
            text="Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )


async def handle_custom_amount_input_kassai_cards(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    """Функция быстрого потока для KassaI Cards."""
    await _handle_custom_amount_input_kassai(
        event, session, "cards", pay_button_text, main_menu_text
    )


async def handle_custom_amount_input_kassai_sbp(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    """Функция быстрого потока для KassaI SBP."""
    await _handle_custom_amount_input_kassai(
        event, session, "sbp", pay_button_text, main_menu_text
    )

