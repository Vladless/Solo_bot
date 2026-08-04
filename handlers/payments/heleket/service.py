import base64
import hashlib
import json
import time

from decimal import ROUND_HALF_UP, Decimal

import aiohttp

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from settings.config import (
    HELEKET_API_KEY,
    HELEKET_CALLBACK_URL,
    HELEKET_MERCHANT_ID,
    HELEKET_RETURN_URL,
    HELEKET_SUCCESS_URL,
    PROVIDERS_ENABLED,
)
from database import async_session_maker, register_pending_payment
from database.models import User
from settings.buttons import BACK, HELEKET, PAY_2
from handlers.payments.keyboards import (
    balance_fallback_kb,
    build_amounts_keyboard,
    parse_amount_from_callback,
    pay_keyboard,
    payment_options_for_user,
)
from settings.texts import (
    ENTER_SUM,
    HELEKET_CRYPTO_DESCRIPTION,
    HELEKET_PAYMENT_MESSAGE,
)
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.currency_rates import (
    format_for_user,
    get_rub_rate,
    pick_currency,
    to_rub,
)
from services.payments.payment_links import register_payment_creator
from services.payments.providers import get_providers


router = Router()


async def get_user_language(session: AsyncSession, tg_id: int) -> str | None:
    """Получает язык пользователя из базы данных"""
    result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


class ReplenishBalanceHeleket(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


HELEKET_METHODS = {
    "crypto": {
        "enable": PROVIDERS_ENABLED.get("HELEKET", False),
        "currency": "USD",
        "to_currency": None,
        "button": HELEKET,
        "desc": HELEKET_CRYPTO_DESCRIPTION,
    },
}


async def process_callback_pay_heleket(
    callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, method_name: str = None
):
    try:
        tg_id = callback_query.from_user.id
        logger.info(f"User {tg_id} initiated Heleket payment.")
        await state.clear()

        if not method_name:
            enabled_methods = [name for name, m in HELEKET_METHODS.items() if m["enable"]]
            if len(enabled_methods) == 1:
                method_name = enabled_methods[0]

        if method_name:
            method = HELEKET_METHODS.get(method_name)
            if not method or not method["enable"]:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text="Ошибка: выбранный способ оплаты недоступен.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                )
                return

            language_code = await get_user_language(session, tg_id)
            opts = await payment_options_for_user(session, tg_id, language_code, force_currency="USD")
            builder = build_amounts_keyboard(
                prefix=f"heleket_{method_name}",
                pattern="{prefix}_amount|{price}",
                back_cb="balance",
                custom_cb=f"heleket_custom_amount|{method_name}",
                opts=opts,
            )

            await edit_or_send_message(
                target_message=callback_query.message,
                text=method["desc"],
                reply_markup=builder,
            )
            await state.update_data(
                heleket_method=method_name,
                message_id=callback_query.message.message_id,
                chat_id=callback_query.message.chat.id,
            )
            await state.set_state(ReplenishBalanceHeleket.choosing_amount)
            return

        builder = InlineKeyboardBuilder()
        for name, method in HELEKET_METHODS.items():
            if method["enable"]:
                builder.row(InlineKeyboardButton(text=method["button"], callback_data=f"heleket_method|{name}"))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text="Выберите способ оплаты через Heleket:",
            reply_markup=builder.as_markup(),
        )
        await state.update_data(
            message_id=callback_query.message.message_id,
            chat_id=callback_query.message.chat.id,
        )
        await state.set_state(ReplenishBalanceHeleket.choosing_method)

    except Exception as e:
        logger.error(f"Error in process_callback_pay_heleket for user {callback_query.message.chat.id}: {e}")
        await callback_query.answer("Произошла ошибка при инициализации платежа. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data.startswith("heleket_method|"))
async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    method = HELEKET_METHODS.get(method_name)

    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    await state.update_data(heleket_method=method_name)
    tg_id = callback_query.from_user.id

    language_code = await get_user_language(session, tg_id)
    opts = await payment_options_for_user(session, tg_id, language_code, force_currency="USD")
    builder = build_amounts_keyboard(
        prefix=f"heleket_{method_name}",
        pattern="{prefix}_amount|{price}",
        back_cb="pay",
        custom_cb=f"heleket_custom_amount|{method_name}",
        opts=opts,
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=method["desc"],
        reply_markup=builder,
    )
    await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
    await state.set_state(ReplenishBalanceHeleket.choosing_amount)


@router.callback_query(F.data.startswith("heleket_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    await state.update_data(heleket_method=method_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_heleket_crypto"))

    language_code = await get_user_language(session, callback_query.from_user.id)
    currency = pick_currency(language_code)

    currency_text = "рублях (₽)" if currency == "RUB" else "долларах ($)"
    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"Пожалуйста, введите сумму пополнения в {currency_text}.",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(ReplenishBalanceHeleket.entering_custom_amount)


@router.message(ReplenishBalanceHeleket.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    method_name = data.get("heleket_method")
    method = HELEKET_METHODS.get(method_name)

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

        min_amount = 1 if currency == "USD" else 10
        currency_symbol = "$" if currency == "USD" else "₽"

        if user_amount < min_amount:
            await edit_or_send_message(
                target_message=message,
                text=f"❌ Минимальная сумма для оплаты криптовалютой — {currency_symbol}{min_amount}.",
                reply_markup=balance_fallback_kb(),
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
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session_http:
            amount_rub = int(await to_rub(user_amount, "USD", session=session_http))

    await state.update_data(amount=amount_rub)
    payment_url = await generate_heleket_payment_link(amount_rub, message.chat.id, method, session)

    if not payment_url or payment_url == "https://heleket.com/":
        await edit_or_send_message(
            target_message=message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    tg_id = message.from_user.id
    amount_text = await format_for_user(session, tg_id, float(amount_rub), language_code, force_currency="USD")

    await edit_or_send_message(
        target_message=message,
        text=HELEKET_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )

    await state.set_state(ReplenishBalanceHeleket.waiting_for_payment_confirmation)


@router.callback_query(F.data.startswith("heleket_crypto_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    amount = parse_amount_from_callback(callback_query.data, prefixes=["heleket_crypto"])
    if amount is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    method_name = "crypto"
    method = HELEKET_METHODS.get(method_name)

    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    if amount < 10:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Минимальная сумма для оплаты криптовалютой — 10₽ (≈0.1$).",
            reply_markup=balance_fallback_kb(),
        )
        return

    await state.update_data(amount=amount)
    payment_url = await generate_heleket_payment_link(amount, callback_query.message.chat.id, method, session)

    if not payment_url or payment_url == "https://heleket.com/":
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    tg_id = callback_query.from_user.id
    language_code = await get_user_language(session, tg_id)
    amount_text = await format_for_user(session, tg_id, float(amount), language_code, force_currency="USD")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=HELEKET_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )

    await state.set_state(ReplenishBalanceHeleket.waiting_for_payment_confirmation)


async def generate_heleket_payment_link(
    amount: int,
    tg_id: int,
    method: dict,
    session: AsyncSession | None = None,
    *,
    order_id: str | None = None,
    success_url: str | None = None,
    failure_url: str | None = None,
    metadata: dict | None = None,
) -> str:
    """
    Создание платежа в Heleket и получение ссылки на оплату.
    amount — сумма в RUB, method['currency'] — валюта провайдера (обычно USD).
    session — сессия из хендлера; если не передана, создаётся своя (лишняя нагрузка на пул).
    """
    url = "https://api.heleket.com/v1/payment"
    unique_order_id = order_id or f"{int(time.time())}_{tg_id}"

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            pay_cur = str(method["currency"]).upper()

            if pay_cur == "RUB":
                payment_amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                rate = await get_rub_rate(pay_cur, session=http_session)
                payment_amount = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            data = {
                "amount": str(payment_amount),
                "currency": method["currency"],
                "order_id": unique_order_id,
                "url_success": success_url or HELEKET_SUCCESS_URL,
                "url_return": failure_url or HELEKET_RETURN_URL,
                "url_callback": HELEKET_CALLBACK_URL,
                "additional_data": f"tg_id:{tg_id},rub_amount:{amount}",
            }
            if method.get("to_currency"):
                data["to_currency"] = method["to_currency"]

            json_data = json.dumps(data, separators=(",", ":"))
            base64_data = base64.b64encode(json_data.encode("utf-8")).decode("utf-8")
            sign_string = base64_data + HELEKET_API_KEY
            signature = hashlib.md5(sign_string.encode("utf-8")).hexdigest()

            headers = {
                "merchant": HELEKET_MERCHANT_ID,
                "sign": signature,
                "Content-Type": "application/json",
            }

            async with http_session.post(url, headers=headers, data=json_data, timeout=60) as resp:
                if resp.status == 200:
                    try:
                        resp_json = await resp.json()
                        if resp_json.get("state") == 0:
                            payment_url = resp_json.get("result", {}).get("url")
                            if payment_url:
                                await register_pending_payment(
                                    payment_id=unique_order_id,
                                    tg_id=tg_id,
                                    amount=float(amount),
                                    payment_system="heleket",
                                    currency="RUB",
                                    metadata=metadata,
                                )
                                logger.info(f"Heleket payment URL created for user {tg_id}")
                                return payment_url
                            else:
                                logger.error(f"Heleket: No URL in response: {resp_json}")
                                return "https://heleket.com/"
                        else:
                            logger.error(f"Heleket: Unsuccessful response: {resp_json}")
                            return "https://heleket.com/"
                    except Exception as e:
                        logger.error(f"Heleket: Error parsing JSON response: {e}")
                        text = await resp.text()
                        logger.error(f"Heleket: Response content: {text}")
                        return "https://heleket.com/"
                else:
                    try:
                        error_json = await resp.json()
                        logger.error(f"Heleket API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"Heleket API error: status={resp.status}, non-JSON response: {text}")
                    return "https://heleket.com/"
    except Exception as e:
        logger.error(f"Error creating Heleket payment: {e}")
        return "https://heleket.com/"


async def create_link(
    session: AsyncSession,
    tg_id: int,
    amount: float,
    currency: str,
    success_url: str | None,
    failure_url: str | None,
    metadata: dict | None,
) -> tuple[str, str | None]:
    method = HELEKET_METHODS.get("crypto")
    if not method or not method.get("enable"):
        raise ValueError("Heleket недоступен")
    amount_int = int(amount)
    order_id = f"{int(time.time())}_{tg_id}"
    if amount_int < 10:
        raise ValueError("Минимальная сумма для Heleket — 10₽")
    url = await generate_heleket_payment_link(
        amount_int,
        tg_id,
        method,
        session,
        order_id=order_id,
        success_url=success_url,
        failure_url=failure_url,
        metadata=metadata,
    )
    if not url or url == "https://heleket.com/":
        raise ValueError("Не удалось создать платёж Heleket")
    return (url, order_id)


register_payment_creator("HELEKET", create_link)
