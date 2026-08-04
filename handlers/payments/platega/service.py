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
    PLATEGA_API_SECRET,
    PLATEGA_FAIL_URL,
    PLATEGA_MERCHANT_ID,
    PLATEGA_SUCCESS_URL,
)
from core.bootstrap import PAYMENTS_CONFIG
from database import add_payment, async_session_maker
from database.models import User
from settings.buttons import (
    BACK,
    PAY_2,
    PLATEGA_CARDS,
    PLATEGA_CRYPTO,
    PLATEGA_INT,
    PLATEGA_SBP,
)
from handlers.payments.keyboards import (
    balance_fallback_kb,
    build_amounts_keyboard,
    parse_amount_from_callback,
    pay_keyboard,
    payment_options_for_user,
)
from settings.texts import (
    PLATEGA_CARDS_DESCRIPTION,
    PLATEGA_CRYPTO_DESCRIPTION,
    PLATEGA_INT_DESCRIPTION,
    PLATEGA_PAYMENT_MESSAGE,
    PLATEGA_PAYMENT_TITLE,
    PLATEGA_SBP_DESCRIPTION,
)
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.currency_rates import format_for_user, get_rub_rate, pick_currency, to_rub
from services.payments.payment_links import register_payment_creator


router = Router()


PLATEGA_API_URL = "https://app.platega.io/transaction/process"

PLATEGA_MIN_AMOUNTS: dict[str, int] = {
    "sbp": 10,
    "cards": 10,
    "int": 1,
    "crypto": 1,
}


class ReplenishBalancePlatega(StatesGroup):
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


PLATEGA_METHODS: dict[str, dict] = {
    "sbp": {
        "provider_key": "PLATEGA_SBP",
        "method_code": 2,
        "currency": "RUB",
        "button": PLATEGA_SBP,
        "desc": PLATEGA_SBP_DESCRIPTION,
    },
    "cards": {
        "provider_key": "PLATEGA_CARDS",
        "method_code": 11,
        "currency": "RUB",
        "button": PLATEGA_CARDS,
        "desc": PLATEGA_CARDS_DESCRIPTION,
    },
    "int": {
        "provider_key": "PLATEGA_INT",
        "method_code": 12,
        "currency": "USD",
        "button": PLATEGA_INT,
        "desc": PLATEGA_INT_DESCRIPTION,
    },
    "crypto": {
        "provider_key": "PLATEGA_CRYPTO",
        "method_code": 13,
        "currency": "USD",
        "button": PLATEGA_CRYPTO,
        "desc": PLATEGA_CRYPTO_DESCRIPTION,
    },
}


def _platega_method_enabled(method: dict) -> bool:
    return bool(PAYMENTS_CONFIG.get(method["provider_key"], False))


def _platega_credentials_ok() -> bool:
    return bool((PLATEGA_MERCHANT_ID or "").strip()) and bool((PLATEGA_API_SECRET or "").strip())


async def _get_user_language(session: AsyncSession, tg_id: int) -> str | None:
    result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


async def process_callback_pay_platega(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    method_name: str,
):
    try:
        tg_id = callback_query.from_user.id
        await state.clear()

        method = PLATEGA_METHODS.get(method_name)
        if not method or not _platega_method_enabled(method):
            await edit_or_send_message(
                target_message=callback_query.message,
                text="Ошибка: выбранный способ оплаты недоступен.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
            return

        if not _platega_credentials_ok():
            logger.error("[Platega] Не заданы PLATEGA_MERCHANT_ID / PLATEGA_API_SECRET")
            await edit_or_send_message(
                target_message=callback_query.message,
                text="Ошибка: платёжная система временно недоступна.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
            return

        language_code = await _get_user_language(session, tg_id)
        opts = await payment_options_for_user(
            session,
            tg_id,
            language_code,
            force_currency=method["currency"],
        )
        builder = build_amounts_keyboard(
            prefix=f"platega_{method_name}",
            pattern="{prefix}_amount|{price}",
            back_cb="balance",
            custom_cb=f"platega_custom_amount|{method_name}",
            opts=opts,
        )

        await edit_or_send_message(
            target_message=callback_query.message,
            text=method["desc"],
            reply_markup=builder,
        )
        await state.update_data(
            platega_method=method_name,
            message_id=callback_query.message.message_id,
            chat_id=callback_query.message.chat.id,
        )
        await state.set_state(ReplenishBalancePlatega.choosing_amount)

    except Exception as e:
        logger.error(f"[Platega] Ошибка в process_callback_pay_platega для {callback_query.from_user.id}: {e}")
        await callback_query.answer(
            "Произошла ошибка при инициализации платежа. Попробуйте позже.",
            show_alert=True,
        )


@router.callback_query(F.data == "pay_platega_sbp")
async def _pay_platega_sbp(cb: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_platega(cb, state, session, "sbp")


@router.callback_query(F.data == "pay_platega_cards")
async def _pay_platega_cards(cb: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_platega(cb, state, session, "cards")


@router.callback_query(F.data == "pay_platega_int")
async def _pay_platega_int(cb: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_platega(cb, state, session, "int")


@router.callback_query(F.data == "pay_platega_crypto")
async def _pay_platega_crypto(cb: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_platega(cb, state, session, "crypto")


@router.callback_query(F.data.startswith("platega_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    method = PLATEGA_METHODS.get(method_name)
    if not method:
        return

    await state.update_data(platega_method=method_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"pay_platega_{method_name}"))

    language_code = await _get_user_language(session, callback_query.from_user.id)
    currency = pick_currency(language_code)
    currency_text = "рублях (₽)" if currency == "RUB" else "долларах ($)"

    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"Пожалуйста, введите сумму пополнения в {currency_text}.",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(ReplenishBalancePlatega.entering_custom_amount)


@router.message(ReplenishBalancePlatega.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    method_name = data.get("platega_method")
    method = PLATEGA_METHODS.get(method_name)

    if not method or not _platega_method_enabled(method):
        await edit_or_send_message(
            target_message=message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    language_code = await _get_user_language(session, message.from_user.id)
    currency = pick_currency(language_code)

    try:
        user_amount = int(message.text.strip())
        if user_amount <= 0:
            raise ValueError

        min_amount = PLATEGA_MIN_AMOUNTS.get(method_name, 10)
        if currency == "USD":
            min_amount = 1
        currency_symbol = "$" if currency == "USD" else "₽"

        if user_amount < min_amount:
            await edit_or_send_message(
                target_message=message,
                text=f"❌ Минимальная сумма для оплаты через Platega — {currency_symbol}{min_amount}.",
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
    payment_url = await generate_platega_payment_link(amount_rub, message.chat.id, method, session)

    if not payment_url:
        await edit_or_send_message(
            target_message=message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    amount_text = await format_for_user(
        session,
        message.from_user.id,
        float(amount_rub),
        language_code,
        force_currency=method["currency"],
    )

    await edit_or_send_message(
        target_message=message,
        text=PLATEGA_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )
    await state.set_state(ReplenishBalancePlatega.waiting_for_payment_confirmation)


@router.callback_query(
    F.data.startswith("platega_sbp_amount|")
    | F.data.startswith("platega_cards_amount|")
    | F.data.startswith("platega_int_amount|")
    | F.data.startswith("platega_crypto_amount|")
)
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    prefixes = ["platega_sbp", "platega_cards", "platega_int", "platega_crypto"]
    amount = parse_amount_from_callback(callback_query.data, prefixes=prefixes)
    if amount is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    method_name = next((p.removeprefix("platega_") for p in prefixes if callback_query.data.startswith(f"{p}_amount|")), None)
    method = PLATEGA_METHODS.get(method_name) if method_name else None

    if not method or not _platega_method_enabled(method):
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    min_amount = PLATEGA_MIN_AMOUNTS.get(method_name, 10)
    if amount < min_amount:
        symbol = "$" if method["currency"] == "USD" else "₽"
        await edit_or_send_message(
            target_message=callback_query.message,
            text=f"❌ Минимальная сумма для оплаты через Platega — {symbol}{min_amount}.",
            reply_markup=balance_fallback_kb(),
        )
        return

    await state.update_data(amount=amount)
    payment_url = await generate_platega_payment_link(amount, callback_query.message.chat.id, method, session)

    if not payment_url:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    tg_id = callback_query.from_user.id
    language_code = await _get_user_language(session, tg_id)
    amount_text = await format_for_user(
        session, tg_id, float(amount), language_code, force_currency=method["currency"]
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=PLATEGA_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )
    await state.set_state(ReplenishBalancePlatega.waiting_for_payment_confirmation)


def _platega_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-MerchantId": PLATEGA_MERCHANT_ID,
        "X-Secret": PLATEGA_API_SECRET,
    }


async def generate_platega_payment_link(
    amount: int,
    tg_id: int,
    method: dict,
    session: AsyncSession | None = None,
    *,
    payment_id: str | None = None,
    success_url: str | None = None,
    failure_url: str | None = None,
    metadata: dict | None = None,
) -> str | None:
    if not _platega_credentials_ok():
        logger.error("[Platega] Не заданы PLATEGA_MERCHANT_ID / PLATEGA_API_SECRET")
        return None

    method_code = int(method.get("method_code") or 0)
    currency = str(method.get("currency") or "RUB").upper()
    method_name = next((k for k, v in PLATEGA_METHODS.items() if v is method), None) or ""

    unique_order_id = payment_id or f"plg_{int(time.time())}_{tg_id}_{int(amount)}"

    pending_metadata = dict(metadata or {})
    pending_metadata.setdefault("provider", "platega")
    pending_metadata.setdefault("platega_method", method_name)
    pending_metadata.setdefault("platega_method_code", method_code)

    pending_original_amount: float | None = None

    if currency == "RUB":
        api_amount = float(int(amount))
    else:
        try:
            timeout = aiohttp.ClientTimeout(total=15, connect=10)
            async with aiohttp.ClientSession(timeout=timeout) as http_rates:
                rate = await get_rub_rate(currency, session=http_rates)
            usd_amount = (Decimal(str(amount)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            api_amount = float(usd_amount)
            pending_original_amount = float(usd_amount)
            pending_metadata["platega_currency"] = currency
        except Exception as e:
            logger.error(f"[Platega] Не удалось сконвертировать {amount} RUB → {currency}: {e}")
            return None

    body: dict = {
        "paymentMethod": method_code,
        "paymentDetails": {
            "amount": api_amount,
            "currency": currency,
        },
        "description": PLATEGA_PAYMENT_TITLE,
        "payload": unique_order_id,
    }
    ret_url = success_url or PLATEGA_SUCCESS_URL or ""
    fail_url = failure_url or PLATEGA_FAIL_URL or ""
    if ret_url:
        body["return"] = ret_url
    if fail_url:
        body["failedUrl"] = fail_url

    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(PLATEGA_API_URL, headers=_platega_headers(), json=body) as resp:
                if resp.status not in (200, 201):
                    try:
                        error_json = await resp.json(content_type=None)
                        logger.error(f"[Platega] API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"[Platega] API error: status={resp.status}, non-JSON: {text[:300]}")
                    return None

                try:
                    resp_json = await resp.json(content_type=None)
                except Exception as e:
                    text = await resp.text()
                    logger.error(f"[Platega] Не удалось распарсить JSON: {e}, ответ={text[:300]}")
                    return None

                payment_url = resp_json.get("redirect")
                transaction_id = str(resp_json.get("transactionId") or "")
                if not payment_url or not transaction_id:
                    logger.error(f"[Platega] В ответе нет redirect или transactionId: {resp_json}")
                    return None

                pending_metadata["platega_transaction_id"] = transaction_id
                pending_metadata["platega_order_id"] = unique_order_id

                async with async_session_maker() as db_session:
                    try:
                        await add_payment(
                            session=db_session,
                            tg_id=tg_id,
                            amount=float(int(amount)),
                            payment_system="platega",
                            status="pending",
                            currency="RUB",
                            payment_id=transaction_id,
                            metadata=pending_metadata,
                            original_amount=pending_original_amount,
                        )
                        await db_session.commit()
                    except Exception as e:
                        logger.error(
                            f"[Platega] Не удалось записать pending платёж в БД "
                            f"(transaction_id={transaction_id}, tg_id={tg_id}): {e}"
                        )
                        await db_session.rollback()
                        return None

                logger.info(
                    f"[Platega] Ссылка создана: tg_id={tg_id}, transaction_id={transaction_id}, "
                    f"order_id={unique_order_id}, rub_amount={amount}, "
                    f"api_amount={api_amount} {currency}, method={method_code}"
                )
                return payment_url
    except Exception as e:
        logger.error(f"[Platega] Ошибка создания платежа: {e}")
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
        method = PLATEGA_METHODS.get(method_name)
        if not method or not _platega_method_enabled(method):
            raise ValueError("Способ оплаты Platega недоступен")

        amount_int = int(amount)
        if amount_int <= 0:
            raise ValueError("Сумма должна быть больше нуля")

        min_amount = PLATEGA_MIN_AMOUNTS.get(method_name, 10)
        if amount_int < min_amount:
            symbol = "$" if method["currency"] == "USD" else "₽"
            raise ValueError(f"Минимальная сумма Platega — {symbol}{min_amount}")

        payment_id = f"plg_{int(time.time())}_{tg_id}_{amount_int}"
        url = await generate_platega_payment_link(
            amount_int,
            tg_id,
            method,
            session,
            payment_id=payment_id,
            success_url=success_url,
            failure_url=failure_url,
            metadata=metadata,
        )
        if not url:
            raise ValueError("Не удалось создать платёж Platega")
        return (url, payment_id)

    return create_link


for _name in PLATEGA_METHODS:
    register_payment_creator(PLATEGA_METHODS[_name]["provider_key"], _create_link_factory(_name))
