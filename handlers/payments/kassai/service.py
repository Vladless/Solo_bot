import hashlib
import hmac
import time

import aiohttp
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    KASSAI_API_KEY,
    KASSAI_DOMAIN,
    KASSAI_FAILURE_URL,
    KASSAI_IP,
    KASSAI_SHOP_ID,
    KASSAI_SUCCESS_URL,
    PROVIDERS_ENABLED,
)
from database import add_payment, async_session_maker
from database.models import User
from handlers.buttons import BACK, KASSAI_CARDS, KASSAI_SBP, PAY_2
from handlers.payments.currency_rates import (
    format_for_user,
    pick_currency,
    to_rub,
)
from handlers.payments.keyboards import (
    build_amounts_keyboard,
    parse_amount_from_callback,
    pay_keyboard,
    payment_options_for_user,
)
from handlers.payments.providers import get_providers
from handlers.texts import (
    ENTER_SUM,
    KASSAI_CARDS_DESCRIPTION,
    KASSAI_PAYMENT_MESSAGE,
    KASSAI_SBP_DESCRIPTION,
)
from handlers.utils import edit_or_send_message
from logger import logger

router = Router()


async def get_user_language(session: AsyncSession, tg_id: int) -> str | None:
    """Получает язык пользователя из базы данных."""
    result = await session.execute(
        select(User.language_code).where(User.tg_id == tg_id)
    )
    return result.scalar_one_or_none()


class ReplenishBalanceKassaiState(StatesGroup):
    """Состояния FSM для пополнения баланса через KassaI."""

    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


KASSAI_METHODS = {
    "cards": {"enable": PROVIDERS_ENABLED.get("KASSAI_CARDS", False), "method": 36, "button": KASSAI_CARDS, "desc": KASSAI_CARDS_DESCRIPTION},
    "sbp": {"enable": PROVIDERS_ENABLED.get("KASSAI_SBP", False), "method": 44, "button": KASSAI_SBP, "desc": KASSAI_SBP_DESCRIPTION},
}


@router.callback_query(F.data == "pay_kassai")
async def process_callback_pay_kassai(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    method_name: str = None,
):
    """Обработчик callback для инициализации платежа через KassaI."""
    try:
        tg_id = callback_query.from_user.id
        logger.info(f"User {tg_id} initiated KassaAI payment.")
        await state.clear()

        if method_name:
            method = KASSAI_METHODS.get(method_name)
            if not method or not method["enable"]:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text="Ошибка: выбранный способ оплаты недоступен.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                )
                return

            language_code = await get_user_language(session, tg_id)
            opts = await payment_options_for_user(
                session, tg_id, language_code, force_currency="RUB"
            )
            builder = build_amounts_keyboard(
                prefix=f"kassai_{method_name}",
                pattern="{prefix}_amount|{price}",
                back_cb="balance",
                custom_cb=f"kassai_custom_amount|{method_name}",
                opts=opts,
            )

            await edit_or_send_message(
                target_message=callback_query.message,
                text=method["desc"],
                reply_markup=builder,
            )
            await state.update_data(
                kassai_method=method_name,
                message_id=callback_query.message.message_id,
                chat_id=callback_query.message.chat.id,
            )
            await state.set_state(ReplenishBalanceKassaiState.choosing_amount)
            return

        builder = InlineKeyboardBuilder()
        for name, method in KASSAI_METHODS.items():
            if method["enable"]:
                builder.row(
                    InlineKeyboardButton(
                        text=method["button"],
                        callback_data=f"kassai_method|{name}",
                    )
                )
        builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text="Выберите способ оплаты через KassaAI:",
            reply_markup=builder.as_markup(),
        )
        await state.update_data(
            message_id=callback_query.message.message_id,
            chat_id=callback_query.message.chat.id,
        )
        await state.set_state(ReplenishBalanceKassaiState.choosing_method)

    except Exception as e:
        logger.error(
            f"Error in process_callback_pay_kassai for user "
            f"{callback_query.message.chat.id}: {e}"
        )
        await callback_query.answer(
            "Произошла ошибка при инициализации платежа. Попробуйте позже.",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("kassai_method|"))
async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    method = KASSAI_METHODS.get(method_name)

    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    await state.update_data(kassai_method=method_name)
    tg_id = callback_query.from_user.id

    language_code = await get_user_language(session, tg_id)
    opts = await payment_options_for_user(
        session, tg_id, language_code, force_currency="RUB"
    )
    builder = build_amounts_keyboard(
        prefix=f"kassai_{method_name}",
        pattern="{prefix}_amount|{price}",
        back_cb="pay_kassai",
        custom_cb=f"kassai_custom_amount|{method_name}",
        opts=opts
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=method["desc"],
        reply_markup=builder,
    )
    await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
    await state.set_state(ReplenishBalanceKassaiState.choosing_amount)


@router.callback_query(F.data.startswith("kassai_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    await state.update_data(kassai_method=method_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"pay_kassai_{method_name}"))

    language_code = await get_user_language(session, callback_query.from_user.id)
    currency = pick_currency(language_code)
    
    currency_text = "рублях (₽)" if currency == "RUB" else "долларах ($)"
    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"Пожалуйста, введите сумму пополнения в {currency_text}.",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(ReplenishBalanceKassaiState.entering_custom_amount)


@router.message(ReplenishBalanceKassaiState.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    method_name = data.get("kassai_method")
    method = KASSAI_METHODS.get(method_name)

    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    language_code = await get_user_language(session, message.from_user.id)
    currency = pick_currency(language_code)

    try:
        user_amount = int(message.text.strip())
        if user_amount <= 0:
            raise ValueError
        
        if method_name == "cards":
            min_amount = 1 if currency == "USD" else 50
            currency_symbol = "$" if currency == "USD" else "₽"
            if user_amount < min_amount:
                await edit_or_send_message(
                    target_message=message,
                    text=f"❌ Минимальная сумма для оплаты картой — {currency_symbol}{min_amount}.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                )
                return
        elif method_name == "sbp":
            min_amount = 1 if currency == "USD" else 10
            currency_symbol = "$" if currency == "USD" else "₽"
            if user_amount < min_amount:
                await edit_or_send_message(
                    target_message=message,
                    text=f"❌ Минимальная сумма для оплаты через СБП — {currency_symbol}{min_amount}.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                )
                return
    except Exception:
        await edit_or_send_message(
            target_message=message,
            text="❌ Некорректная сумма. Введите целое число больше 0.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    if currency == "RUB":
        amount_rub = user_amount
    else: 
        async with aiohttp.ClientSession() as session_http:
            amount_rub = int(await to_rub(user_amount, "USD", session=session_http))

    await state.update_data(amount=amount_rub)
    payment_url = await generate_kassai_payment_link(amount_rub, message.chat.id, method)

    if not payment_url or payment_url == "https://fk.life/":
        await edit_or_send_message(
            target_message=message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    tg_id = message.from_user.id
    amount_text = await format_for_user(session, tg_id, float(amount_rub), language_code, force_currency="RUB")

    await edit_or_send_message(
        target_message=message,
        text=KASSAI_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )

    await state.set_state(ReplenishBalanceKassaiState.waiting_for_payment_confirmation)


@router.callback_query(F.data.startswith("kassai_cards_amount|") | F.data.startswith("kassai_sbp_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    amount = parse_amount_from_callback(callback_query.data, prefixes=["kassai_cards", "kassai_sbp"])
    if amount is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    method_name = "cards" if callback_query.data.startswith("kassai_cards") else "sbp"
    method = KASSAI_METHODS.get(method_name)

    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    if method_name == "cards" and amount < 50:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Минимальная сумма для оплаты картой — 50₽.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return
    elif method_name == "sbp" and amount < 10:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Минимальная сумма для оплаты через СБП — 10₽.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    await state.update_data(amount=amount)
    payment_url = await generate_kassai_payment_link(amount, callback_query.message.chat.id, method)

    if not payment_url or payment_url == "https://fk.life/":
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    tg_id = callback_query.from_user.id
    language_code = await get_user_language(session, tg_id)
    amount_text = await format_for_user(session, tg_id, float(amount), language_code, force_currency="RUB")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=KASSAI_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )

    await state.set_state(ReplenishBalanceKassaiState.waiting_for_payment_confirmation)


async def generate_kassai_payment_link(amount: int, tg_id: int, method: dict) -> str:
    """
    Создание заказа в KassaAI и получение ссылки на оплату
    """
    nonce = int(time.time())
    unique_payment_id = f"{nonce}_{tg_id}"
    url = "https://api.fk.life/v1/orders/create"

    headers = {"Content-Type": "application/json"}

    client_email = f"{tg_id}@{KASSAI_DOMAIN}"
    client_ip = KASSAI_IP

    data_for_signature = {
        "shopId": KASSAI_SHOP_ID,
        "nonce": nonce,
        "i": method["method"],
        "email": client_email,
        "ip": client_ip,
        "amount": int(amount),
        "currency": "RUB",
        "success_url": KASSAI_SUCCESS_URL,
        "failure_url": KASSAI_FAILURE_URL,
        "paymentId": unique_payment_id,
    }

    sign_string = "|".join(str(data_for_signature[k]) for k in sorted(data_for_signature.keys()))
    signature = hmac.new(KASSAI_API_KEY.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha256).hexdigest()

    data = {**data_for_signature, "signature": signature}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=60) as resp:
                if resp.status == 200:
                    try:
                        resp_json = await resp.json()
                        if resp_json.get("type") == "success":
                            payment_url = resp_json.get("location")
                            if payment_url:
                                async with async_session_maker() as dbs:
                                    await add_payment(
                                        session=dbs,
                                        tg_id=tg_id,
                                        amount=float(amount),
                                        payment_system="KASSAI",
                                        status="pending",
                                        currency="RUB",
                                        payment_id=unique_payment_id,
                                    )
                                logger.info(f"KassaAI payment URL created for user {tg_id}")
                                return payment_url
                            logger.error(f"KassaAI: No location in response: {resp_json}")
                            return "https://fk.life/"
                        logger.error(f"KassaAI: Unsuccessful response: {resp_json}")
                        return "https://fk.life/"
                    except Exception as e:
                        logger.error(f"KassaAI: Error parsing JSON response: {e}")
                        text = await resp.text()
                        logger.error(f"KassaAI: Response content: {text}")
                        return "https://fk.life/"
                else:
                    try:
                        error_json = await resp.json()
                        logger.error(f"KassaAI API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"KassaAI API error: status={resp.status}, non-JSON response: {text}")
                    return "https://fk.life/"
    except Exception as e:
        logger.error(f"Error creating KassaAI order: {e}")
        return "https://fk.life/"
