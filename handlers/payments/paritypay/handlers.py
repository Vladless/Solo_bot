from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_temporary_data
from database.models import User
from settings.buttons import MAIN_MENU, PAY_2
from settings.texts import DEFAULT_PAYMENT_MESSAGE
from handlers.payments.keyboards import balance_fallback_kb
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.currency_rates import format_for_user

from ..constants import ALLOWED_TEMP_PAYMENT_STATES
from .service import (
    PARITYPAY_METHODS,
    generate_paritypay_payment_link,
    process_callback_pay_paritypay,
    router as service_router,
)


router = Router(name="paritypay_router")
router.include_router(service_router)


@router.callback_query(F.data == "pay_paritypay_sbp")
async def handle_pay_paritypay_sbp(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_paritypay(callback_query, state, session, method_name="sbp")


async def _handle_custom_amount_input_paritypay(
    event,
    session: AsyncSession,
    method_name: str,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    message = event.message
    from_user = event.from_user
    tg_id = from_user.id

    temp_data = await get_temporary_data(session, tg_id)
    if not temp_data or temp_data["state"] not in ALLOWED_TEMP_PAYMENT_STATES:
        await edit_or_send_message(target_message=message, text="❌ Не удалось получить данные для оплаты.")
        return

    amount = int(temp_data["data"].get("required_amount", 0))
    if amount <= 0:
        await edit_or_send_message(target_message=message, text="❌ Не удалось определить сумму оплаты.")
        return

    method = PARITYPAY_METHODS.get(method_name)
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=message,
            text="❌ Способ оплаты ParityPay временно недоступен.",
        )
        return

    if amount < method["min_amount"]:
        await edit_or_send_message(
            target_message=message,
            text=f"❌ Минимальная сумма для оплаты — {method['min_amount']}₽.",
            reply_markup=balance_fallback_kb(),
        )
        return

    try:
        payment_url = await generate_paritypay_payment_link(amount, tg_id, method, session)
        if not payment_url:
            await edit_or_send_message(
                target_message=message,
                text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            )
            return

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=pay_button_text, url=payment_url)],
                [InlineKeyboardButton(text=main_menu_text, callback_data="profile")],
            ]
        )
        result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
        language_code = result.scalar_one_or_none()
        amount_text = await format_for_user(session, tg_id, float(amount), language_code, force_currency="RUB")
        await edit_or_send_message(
            target_message=message,
            text=DEFAULT_PAYMENT_MESSAGE.format(amount=amount_text),
            reply_markup=markup,
        )
    except Exception as e:
        logger.error(f"Ошибка при создании платежа ParityPay {method_name} для пользователя {tg_id}: {e}")
        await edit_or_send_message(
            target_message=message,
            text="Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )


async def handle_custom_amount_input_paritypay_sbp(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    await _handle_custom_amount_input_paritypay(event, session, "sbp", pay_button_text, main_menu_text)
