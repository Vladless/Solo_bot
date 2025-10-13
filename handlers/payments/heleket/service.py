import base64
import hashlib
import json
import time
from decimal import Decimal, ROUND_HALF_UP

import aiohttp
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from handlers.payments.currency_rates import format_for_user

from config import (
    HELEKET_API_KEY,
    HELEKET_CALLBACK_URL,
    HELEKET_MERCHANT_ID,
    HELEKET_RETURN_URL,
    HELEKET_SUCCESS_URL,
    PROVIDERS_ENABLED,
)
from handlers.payments.providers import get_providers
from handlers.payments.currency_rates import get_rub_rate
from handlers.buttons import BACK, HELEKET, PAY_2
from handlers.texts import ENTER_SUM, HELEKET_CRYPTO_DESCRIPTION, HELEKET_PAYMENT_MESSAGE
from handlers.payments.keyboards import build_amounts_keyboard, payment_options_for_user, parse_amount_from_callback, pay_keyboard
from handlers.utils import edit_or_send_message
from database import add_payment, async_session_maker
from database.models import User
from logger import logger


router = Router()


async def get_user_language(session: AsyncSession, tg_id: int) -> str | None:
    """Получает язык пользователя из базы данных"""
    from sqlalchemy import select
    result = await session.execute(
        select(User.language_code).where(User.tg_id == tg_id)
    )
    return result.scalar_one_or_none()


class ReplenishBalanceHeleket(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


PROVIDERS = get_providers(PROVIDERS_ENABLED)
HELEKET_PAYMENT_METHODS = [
    {
        "enable": bool(PROVIDERS.get("HELEKET", {}).get("enabled")),
        "currency": (PROVIDERS.get("HELEKET", {}).get("currency") or "USD"),
        "to_currency": None,
        "name": "crypto",
        "button": HELEKET,
        "desc": HELEKET_CRYPTO_DESCRIPTION,
    },
]


async def process_callback_pay_heleket(
    callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession, method_name: str = None
):
    try:
        tg_id = callback_query.from_user.id
        logger.info(f"User {tg_id} initiated Heleket payment.")
        await state.clear()

        if not method_name:
            enabled_methods = [m["name"] for m in HELEKET_PAYMENT_METHODS if m["enable"]]
            if len(enabled_methods) == 1:
                method_name = enabled_methods[0]

        if method_name:
            method = next((m for m in HELEKET_PAYMENT_METHODS if m["name"] == method_name and m["enable"]), None)
            if not method:
                try:
                    await callback_query.message.delete()
                except Exception:
                    pass
                await callback_query.message.answer(
                    text="Ошибка: выбранный способ оплаты недоступен.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                )
                return

            language_code = await get_user_language(session, tg_id)
            opts = await payment_options_for_user(session, tg_id, language_code)
            builder = build_amounts_keyboard(
                prefix=f"heleket_{method_name}",
                pattern="{prefix}_amount|{price}",
                back_cb="balance",
                custom_cb=f"heleket_custom_amount|{method_name}",
                opts=opts
            )

            try:
                await callback_query.message.delete()
            except Exception:
                pass
            new_msg = await callback_query.message.answer(
                text=method["desc"],
                reply_markup=builder,
            )

            await state.update_data(
                heleket_method=method_name,
                message_id=new_msg.message_id,
                chat_id=new_msg.chat.id,
            )
            await state.set_state(ReplenishBalanceHeleket.choosing_amount)
            return

        builder = InlineKeyboardBuilder()
        for method in HELEKET_PAYMENT_METHODS:
            if method["enable"]:
                builder.row(
                    InlineKeyboardButton(text=method["button"], callback_data=f"heleket_method|{method['name']}")
                )
        builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

        try:
            await callback_query.message.delete()
        except Exception:
            pass
        new_msg = await callback_query.message.answer(
            text="Выберите способ оплаты через Heleket:",
            reply_markup=builder.as_markup(),
        )
        await state.update_data(message_id=new_msg.message_id, chat_id=new_msg.chat.id)
        await state.set_state(ReplenishBalanceHeleket.choosing_method)

    except Exception as e:
        logger.error(f"Error in process_callback_pay_heleket for user {callback_query.message.chat.id}: {e}")
        await callback_query.answer("Произошла ошибка при инициализации платежа. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data.startswith("heleket_method|"))
async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    method = next((m for m in HELEKET_PAYMENT_METHODS if m["name"] == method_name), None)

    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    await state.update_data(heleket_method=method_name)
    tg_id = callback_query.from_user.id

    language_code = await get_user_language(session, tg_id)
    opts = await payment_options_for_user(session, tg_id, language_code)
    builder = build_amounts_keyboard(
        prefix=f"heleket_{method_name}",
        pattern="{prefix}_amount|{price}",
        back_cb="pay",
        custom_cb=f"heleket_custom_amount|{method_name}",
        opts=opts
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=method["desc"],
        reply_markup=builder,
        force_text=True,
    )
    await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
    await state.set_state(ReplenishBalanceHeleket.choosing_amount)


@router.callback_query(F.data.startswith("heleket_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    await state.update_data(heleket_method=method_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_heleket_crypto"))

    from ..currency_rates import pick_currency
    language_code = await get_user_language(session, callback_query.from_user.id)
    currency = pick_currency(language_code)
    
    currency_text = "рублях (₽)" if currency == "RUB" else "долларах ($)"
    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"Пожалуйста, введите сумму пополнения в {currency_text}.",
        reply_markup=builder.as_markup(),
        force_text=True,
    )
    await state.set_state(ReplenishBalanceHeleket.entering_custom_amount)


@router.message(ReplenishBalanceHeleket.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    method_name = data.get("heleket_method")
    method = next((m for m in HELEKET_PAYMENT_METHODS if m["name"] == method_name), None)

    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    from ..currency_rates import pick_currency
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
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                force_text=True,
            )
            return
    except Exception:
        await edit_or_send_message(
            target_message=message,
            text="❌ Некорректная сумма. Введите целое число больше 0.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    from ..currency_rates import to_rub
    if currency == "RUB":
        amount_rub = user_amount
    else: 
        async with aiohttp.ClientSession() as session_http:
            amount_rub = int(await to_rub(user_amount, "USD", session=session_http))

    await state.update_data(amount=amount_rub)
    payment_url = await generate_heleket_payment_link(amount_rub, message.chat.id, method)

    if not payment_url or payment_url == "https://heleket.com/":
        await edit_or_send_message(
            target_message=message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    from ..currency_rates import format_for_user
    tg_id = message.from_user.id
    amount_text = await format_for_user(session, tg_id, float(amount_rub), language_code)

    await edit_or_send_message(
        target_message=message,
        text=HELEKET_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
        force_text=True,
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
            force_text=True,
        )
        return

    method_name = "crypto"
    method = next((m for m in HELEKET_PAYMENT_METHODS if m["name"] == method_name), None)

    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    if amount < 10:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Минимальная сумма для оплаты криптовалютой — 10₽ (≈0.1$).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    await state.update_data(amount=amount)
    payment_url = await generate_heleket_payment_link(amount, callback_query.message.chat.id, method)

    if not payment_url or payment_url == "https://heleket.com/":
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            force_text=True,
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    tg_id = callback_query.from_user.id
    language_code = await get_user_language(session, tg_id)
    amount_text = await format_for_user(session, tg_id, float(amount), language_code)

    await edit_or_send_message(
        target_message=callback_query.message,
        text=HELEKET_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
        force_text=True,
    )

    await state.set_state(ReplenishBalanceHeleket.waiting_for_payment_confirmation)


async def generate_heleket_payment_link(amount: int, tg_id: int, method: dict) -> str:
    """
    Создание платежа в Heleket и получение ссылки на оплату.
    amount — сумма в RUB, method['currency'] — валюта провайдера (обычно USD).
    """
    url = "https://api.heleket.com/v1/payment"
    unique_order_id = f"{int(time.time())}_{tg_id}"

    try:
        async with aiohttp.ClientSession() as session:
            pay_cur = str(method["currency"]).upper()

            if pay_cur == "RUB":
                payment_amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                rate = await get_rub_rate(pay_cur, session=session)
                payment_amount = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            async with async_session_maker() as dbs:
                await add_payment(
                    session=dbs,
                    tg_id=tg_id,
                    amount=float(amount),
                    payment_system="HELEKET",
                    status="pending",
                    currency="RUB",
                    payment_id=unique_order_id,
                )

            data = {
                "amount": str(payment_amount),
                "currency": method["currency"],
                "order_id": unique_order_id,
                "url_success": HELEKET_SUCCESS_URL,
                "url_return": HELEKET_RETURN_URL,
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

            async with session.post(url, headers=headers, data=json_data, timeout=60) as resp:
                if resp.status == 200:
                    try:
                        resp_json = await resp.json()
                        if resp_json.get("state") == 0:
                            payment_url = resp_json.get("result", {}).get("url")
                            if payment_url:
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







