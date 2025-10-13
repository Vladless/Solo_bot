from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from handlers.buttons import PAY_2, MAIN_MENU
from handlers.texts import DEFAULT_PAYMENT_MESSAGE
from handlers.utils import edit_or_send_message
from handlers.payments.currency_rates import format_for_user
from database import get_temporary_data
from logger import logger

from .service import KASSAI_PAYMENT_METHODS, generate_kassai_payment_link, process_callback_pay_kassai
from .service import router as service_router

router = Router(name="kassai_router")
router.include_router(service_router)


@router.callback_query(F.data == "pay_kassai_cards")
async def handle_pay_kassai_cards(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_kassai(callback_query, state, session, method_name="cards")


@router.callback_query(F.data == "pay_kassai_sbp")
async def handle_pay_kassai_sbp(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_kassai(callback_query, state, session, method_name="sbp")


async def handle_custom_amount_input_kassai_cards(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    """
    Функция быстрого потока для KassaI Cards - принимает недостающую сумму и формирует платеж картами.
    Работает с временными данными из fast_payment_flow для создания/продления/подарка.
    """
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
    
    if amount < 50:
        await edit_or_send_message(target_message=message, text="❌ Минимальная сумма для оплаты картой — 50₽.")
        return
    
    method = next((m for m in KASSAI_PAYMENT_METHODS if m["name"] == "cards" and m["enable"]), None)
    if not method:
        await edit_or_send_message(target_message=message, text="❌ Оплата картами KassaAI временно недоступна.")
        return

    try:
        payment_url = await generate_kassai_payment_link(amount, tg_id, method)
        
        if not payment_url or payment_url == "https://fk.life/":
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

        from database.models import User
        from sqlalchemy import select
        result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
        language_code = result.scalar_one_or_none()
        amount_text = await format_for_user(session, tg_id, float(amount), language_code)
        text_out = DEFAULT_PAYMENT_MESSAGE.format(amount=amount_text)

        await edit_or_send_message(target_message=message, text=text_out, reply_markup=markup)
    except Exception as e:
        logger.error(f"Ошибка при создании платежа KassaAI Cards для пользователя {tg_id}: {e}")
        await edit_or_send_message(
            target_message=message,
            text="Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )


async def handle_custom_amount_input_kassai_sbp(
    event,
    session: AsyncSession,
    pay_button_text: str = PAY_2,
    main_menu_text: str = MAIN_MENU,
):
    """
    Функция быстрого потока для KassaI SBP - принимает недостающую сумму и формирует платеж через СБП.
    Работает с временными данными из fast_payment_flow для создания/продления/подарка.
    """
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
    
    if amount < 10:
        await edit_or_send_message(target_message=message, text="❌ Минимальная сумма для оплаты через СБП — 10₽.")
        return
    
    method = next((m for m in KASSAI_PAYMENT_METHODS if m["name"] == "sbp" and m["enable"]), None)
    if not method:
        await edit_or_send_message(target_message=message, text="❌ Оплата через СБП KassaAI временно недоступна.")
        return

    try:
        payment_url = await generate_kassai_payment_link(amount, tg_id, method)
        
        if not payment_url or payment_url == "https://fk.life/":
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

        from database.models import User
        from sqlalchemy import select
        result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
        language_code = result.scalar_one_or_none()
        amount_text = await format_for_user(session, tg_id, float(amount), language_code)
        text_out = DEFAULT_PAYMENT_MESSAGE.format(amount=amount_text)

        await edit_or_send_message(target_message=message, text=text_out, reply_markup=markup)
    except Exception as e:
        logger.error(f"Ошибка при создании платежа KassaAI SBP для пользователя {tg_id}: {e}")
        await edit_or_send_message(
            target_message=message,
            text="Произошла ошибка при создании платежа. Попробуйте позже.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
