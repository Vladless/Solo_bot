import hashlib
import hmac
import json
import time

import aiohttp

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from settings.config import (
    PARITYPAY_API_SECRET_KEY,
    PARITYPAY_API_URL,
    PARITYPAY_CALLBACK_URL,
    PARITYPAY_FAIL_URL,
    PARITYPAY_SHOP_ID,
    PARITYPAY_SUCCESS_URL,
    PROVIDERS_ENABLED,
)
from database import register_pending_payment
from database.models import User
from settings.buttons import BACK, PARITYPAY_SBP, PAY_2
from handlers.payments.keyboards import (
    balance_fallback_kb,
    build_amounts_keyboard,
    parse_amount_from_callback,
    pay_keyboard,
    payment_options_for_user,
)
from settings.texts import (
    PARITYPAY_PAYMENT_MESSAGE,
    PARITYPAY_SBP_DESCRIPTION,
)
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.currency_rates import format_for_user
from services.payments.payment_links import register_payment_creator


router = Router()


async def get_user_language(session: AsyncSession, tg_id: int) -> str | None:
    result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


class ReplenishBalanceParityPay(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


PARITYPAY_METHODS = {
    "sbp": {
        "enable": PROVIDERS_ENABLED.get("PARITYPAY_SBP", False),
        "service": "sbp",
        "button": PARITYPAY_SBP,
        "desc": PARITYPAY_SBP_DESCRIPTION,
        "min_amount": 10,
    },
}


def _build_signature_string(payload: dict) -> str:
    parts: list[str] = []
    for key in sorted(payload.keys()):
        value = payload[key]
        if value is None:
            parts.append("")
        elif isinstance(value, bool):
            parts.append("1" if value else "0")
        else:
            parts.append(str(value))
    return "".join(parts)


def _sign_request(payload: dict) -> str:
    sign_string = _build_signature_string(payload)
    return hmac.new(
        PARITYPAY_API_SECRET_KEY.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def process_callback_pay_paritypay(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    method_name: str | None = None,
):
    try:
        tg_id = callback_query.from_user.id
        logger.info(f"User {tg_id} initiated ParityPay payment.")
        await state.clear()

        if method_name:
            method = PARITYPAY_METHODS.get(method_name)
            if not method or not method["enable"]:
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text="Ошибка: выбранный способ оплаты недоступен.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                )
                return

            language_code = await get_user_language(session, tg_id)
            opts = await payment_options_for_user(session, tg_id, language_code, force_currency="RUB")
            builder = build_amounts_keyboard(
                prefix=f"paritypay_{method_name}",
                pattern="{prefix}_amount|{price}",
                back_cb="balance",
                custom_cb=f"paritypay_custom_amount|{method_name}",
                opts=opts,
            )

            await edit_or_send_message(
                target_message=callback_query.message,
                text=method["desc"],
                reply_markup=builder,
            )
            await state.update_data(
                paritypay_method=method_name,
                message_id=callback_query.message.message_id,
                chat_id=callback_query.message.chat.id,
            )
            await state.set_state(ReplenishBalanceParityPay.choosing_amount)
            return

        builder = InlineKeyboardBuilder()
        for name, method in PARITYPAY_METHODS.items():
            if method["enable"]:
                builder.row(InlineKeyboardButton(text=method["button"], callback_data=f"paritypay_method|{name}"))
        builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text="Выберите способ оплаты ParityPay:",
            reply_markup=builder.as_markup(),
        )
        await state.update_data(
            message_id=callback_query.message.message_id,
            chat_id=callback_query.message.chat.id,
        )
        await state.set_state(ReplenishBalanceParityPay.choosing_method)
    except Exception as e:
        logger.error(f"Error in process_callback_pay_paritypay for user {callback_query.from_user.id}: {e}")
        await callback_query.answer("Произошла ошибка при инициализации платежа. Попробуйте позже.", show_alert=True)


@router.callback_query(F.data.startswith("paritypay_method|"))
async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    method = PARITYPAY_METHODS.get(method_name)
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    await state.update_data(paritypay_method=method_name)
    tg_id = callback_query.from_user.id
    language_code = await get_user_language(session, tg_id)
    opts = await payment_options_for_user(session, tg_id, language_code, force_currency="RUB")
    builder = build_amounts_keyboard(
        prefix=f"paritypay_{method_name}",
        pattern="{prefix}_amount|{price}",
        back_cb="pay",
        custom_cb=f"paritypay_custom_amount|{method_name}",
        opts=opts,
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=method["desc"],
        reply_markup=builder,
    )
    await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
    await state.set_state(ReplenishBalanceParityPay.choosing_amount)


@router.callback_query(F.data.startswith("paritypay_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    await state.update_data(paritypay_method=method_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data="pay_paritypay"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text="Пожалуйста, введите сумму пополнения в рублях (₽).",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(ReplenishBalanceParityPay.entering_custom_amount)


@router.message(ReplenishBalanceParityPay.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    method_name = data.get("paritypay_method")
    method = PARITYPAY_METHODS.get(method_name)
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    try:
        user_amount = int(message.text.strip())
        if user_amount <= 0:
            raise ValueError
        if user_amount < method["min_amount"]:
            await edit_or_send_message(
                target_message=message,
                text=f"❌ Минимальная сумма для оплаты — {method['min_amount']}₽.",
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

    await state.update_data(amount=user_amount)
    payment_url = await generate_paritypay_payment_link(user_amount, message.from_user.id, method, session)
    if not payment_url:
        await edit_or_send_message(
            target_message=message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")
    tg_id = message.from_user.id
    language_code = await get_user_language(session, tg_id)
    amount_text = await format_for_user(session, tg_id, float(user_amount), language_code, force_currency="RUB")
    await edit_or_send_message(
        target_message=message,
        text=PARITYPAY_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )
    await state.set_state(ReplenishBalanceParityPay.waiting_for_payment_confirmation)


@router.callback_query(F.data.startswith("paritypay_sbp_amount|"))
async def process_amount_selection_sbp(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await _process_amount_selection(callback_query, state, session, "sbp")


async def _process_amount_selection(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    method_name: str,
):
    amount = parse_amount_from_callback(callback_query.data, prefixes=[f"paritypay_{method_name}"])
    if amount is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    method = PARITYPAY_METHODS.get(method_name)
    if not method or not method["enable"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    if amount < method["min_amount"]:
        await edit_or_send_message(
            target_message=callback_query.message,
            text=f"❌ Минимальная сумма для оплаты — {method['min_amount']}₽.",
            reply_markup=balance_fallback_kb(),
        )
        return

    await state.update_data(amount=amount)
    payment_url = await generate_paritypay_payment_link(amount, callback_query.from_user.id, method, session)
    if not payment_url:
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
        text=PARITYPAY_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )
    await state.set_state(ReplenishBalanceParityPay.waiting_for_payment_confirmation)


async def generate_paritypay_payment_link(
    amount: int,
    tg_id: int,
    method: dict,
    session: AsyncSession | None = None,
    *,
    order_id: str | None = None,
    success_url: str | None = None,
    fail_url: str | None = None,
    metadata: dict | None = None,
) -> str | None:
    unique_order_id = order_id or f"{int(time.time())}_{tg_id}"
    payload = {
        "shop_id": PARITYPAY_SHOP_ID,
        "amount": int(amount),
        "order_id": unique_order_id,
        "service": method["service"],
        "success_url": success_url or PARITYPAY_SUCCESS_URL or "",
        "fail_url": fail_url or PARITYPAY_FAIL_URL or "",
        "callback_url": PARITYPAY_CALLBACK_URL or "",
        "user_hash": str(tg_id),
    }
    payload = {k: v for k, v in payload.items() if v not in (None, "")}
    signature = _sign_request(payload)
    headers = {"Content-Type": "application/json", "X-SIGNATURE": signature}
    url = f"{PARITYPAY_API_URL.rstrip('/')}/invoice/create"

    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(url, headers=headers, data=json.dumps(payload)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.error(f"ParityPay API error: status={resp.status}, body={text}")
                    return None
                try:
                    resp_json = json.loads(text)
                except Exception as e:
                    logger.error(f"ParityPay: невалидный JSON в ответе ({e}): {text}")
                    return None
                if "error" in resp_json:
                    logger.error(f"ParityPay error: {resp_json}")
                    return None
                payment_url = resp_json.get("link")
                if not payment_url:
                    logger.error(f"ParityPay: пустая ссылка в ответе: {resp_json}")
                    return None
                await register_pending_payment(
                    payment_id=unique_order_id,
                    tg_id=tg_id,
                    amount=float(amount),
                    payment_system="paritypay",
                    currency="RUB",
                    metadata=metadata,
                )
                logger.info(f"ParityPay payment URL created for user {tg_id}, order_id={unique_order_id}")
                return payment_url
    except Exception as e:
        logger.error(f"Error creating ParityPay payment: {e}")
        return None


def _create_link_factory(method_name: str):
    async def create_link(
        session: AsyncSession,
        tg_id: int,
        amount: float,
        currency: str,
        success_url: str | None,
        failure_url: str | None,
        metadata: dict | None,
    ) -> tuple[str, str | None]:
        if currency != "RUB":
            raise ValueError("ParityPay поддерживает только RUB")
        method = PARITYPAY_METHODS.get(method_name)
        if not method or not method.get("enable"):
            raise ValueError("Способ оплаты ParityPay недоступен")
        amount_int = int(amount)
        if amount_int < method["min_amount"]:
            raise ValueError(f"Минимальная сумма — {method['min_amount']}₽")
        order_id = f"{int(time.time())}_{tg_id}"
        url = await generate_paritypay_payment_link(
            amount_int,
            tg_id,
            method,
            session,
            order_id=order_id,
            success_url=success_url,
            fail_url=failure_url,
            metadata=metadata,
        )
        if not url:
            raise ValueError("Не удалось создать платёж ParityPay")
        return (url, order_id)

    return create_link


register_payment_creator("PARITYPAY_SBP", _create_link_factory("sbp"))
