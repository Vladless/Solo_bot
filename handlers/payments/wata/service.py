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

from config import (
    WATA_FAIL_URL,
    WATA_INT_TOKEN,
    WATA_RU_TOKEN,
    WATA_SUCCESS_URL,
)
from core.bootstrap import PAYMENTS_CONFIG
from database import add_payment, async_session_maker
from database.models import User
from handlers.buttons import BACK, PAY_2, WATA_INT, WATA_RU
from handlers.payments.keyboards import (
    balance_fallback_kb,
    build_amounts_keyboard,
    parse_amount_from_callback,
    pay_keyboard,
    payment_options_for_user,
)
from handlers.texts import (
    WATA_INT_DESCRIPTION,
    WATA_PAYMENT_MESSAGE,
    WATA_PAYMENT_TITLE,
    WATA_RU_DESCRIPTION,
)
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.currency_rates import format_for_user, get_rub_rate, pick_currency, to_rub
from services.payments.payment_links import register_payment_creator


router = Router()


WATA_API_LINKS_URL = "https://api.wata.pro/api/h2h/links"

WATA_MIN_AMOUNTS = {
    "ru": 10,
    "int": 1,
}


class ReplenishBalanceWata(StatesGroup):
    choosing_method = State()
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


WATA_METHODS = {
    "ru": {
        "provider_key": "WATA_RU",
        "currency": "RUB",
        "token": WATA_RU_TOKEN,
        "button": WATA_RU,
        "desc": WATA_RU_DESCRIPTION,
    },
    "int": {
        "provider_key": "WATA_INT",
        "currency": "USD",
        "token": WATA_INT_TOKEN,
        "button": WATA_INT,
        "desc": WATA_INT_DESCRIPTION,
    },
}


def _wata_method_enabled(method: dict) -> bool:
    return bool(PAYMENTS_CONFIG.get(method["provider_key"], False))


async def get_user_language(session: AsyncSession, tg_id: int) -> str | None:
    result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


@router.callback_query(F.data == "pay_wata", flags={"popup": True})
async def process_callback_pay_wata(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    method_name: str = None,
):
    try:
        tg_id = callback_query.from_user.id
        logger.info(f"User {tg_id} initiated Wata payment.")
        await state.clear()

        if method_name:
            method = WATA_METHODS.get(method_name)
            if not method or not _wata_method_enabled(method):
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text="Ошибка: выбранный способ оплаты недоступен.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                )
                return

            language_code = await get_user_language(session, tg_id)
            opts = await payment_options_for_user(
                session,
                tg_id,
                language_code,
                force_currency=method["currency"],
            )
            builder = build_amounts_keyboard(
                prefix=f"wata_{method_name}",
                pattern="{prefix}_amount|{price}",
                back_cb="balance",
                custom_cb=f"wata_custom_amount|{method_name}",
                opts=opts,
            )

            await edit_or_send_message(
                target_message=callback_query.message,
                text=method["desc"],
                reply_markup=builder,
            )
            await state.update_data(
                wata_method=method_name,
                message_id=callback_query.message.message_id,
                chat_id=callback_query.message.chat.id,
            )
            await state.set_state(ReplenishBalanceWata.choosing_amount)
            return

        builder = InlineKeyboardBuilder()
        for name, method in WATA_METHODS.items():
            if _wata_method_enabled(method):
                builder.row(
                    InlineKeyboardButton(
                        text=method["button"],
                        callback_data=f"wata_method|{name}",
                    )
                )
        builder.row(InlineKeyboardButton(text=BACK, callback_data="balance"))

        await edit_or_send_message(
            target_message=callback_query.message,
            text="Выберите способ оплаты через WATA:",
            reply_markup=builder.as_markup(),
        )
        await state.update_data(
            message_id=callback_query.message.message_id,
            chat_id=callback_query.message.chat.id,
        )
        await state.set_state(ReplenishBalanceWata.choosing_method)

    except Exception as e:
        logger.error(f"Error in process_callback_pay_wata for user {callback_query.message.chat.id}: {e}")
        await callback_query.answer(
            "Произошла ошибка при инициализации платежа. Попробуйте позже.",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("wata_method|"))
async def process_method_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    method = WATA_METHODS.get(method_name)

    if not method or not _wata_method_enabled(method):
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    await state.update_data(wata_method=method_name)
    tg_id = callback_query.from_user.id

    language_code = await get_user_language(session, tg_id)
    opts = await payment_options_for_user(
        session,
        tg_id,
        language_code,
        force_currency=method["currency"],
    )
    builder = build_amounts_keyboard(
        prefix=f"wata_{method_name}",
        pattern="{prefix}_amount|{price}",
        back_cb="pay_wata",
        custom_cb=f"wata_custom_amount|{method_name}",
        opts=opts,
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=method["desc"],
        reply_markup=builder,
    )
    await state.update_data(message_id=callback_query.message.message_id, chat_id=callback_query.message.chat.id)
    await state.set_state(ReplenishBalanceWata.choosing_amount)


@router.callback_query(F.data.startswith("wata_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    await state.update_data(wata_method=method_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"pay_wata_{method_name}"))

    language_code = await get_user_language(session, callback_query.from_user.id)
    currency = pick_currency(language_code)

    currency_text = "рублях (₽)" if currency == "RUB" else "долларах ($)"
    await edit_or_send_message(
        target_message=callback_query.message,
        text=f"Пожалуйста, введите сумму пополнения в {currency_text}.",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(ReplenishBalanceWata.entering_custom_amount)


@router.message(ReplenishBalanceWata.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    method_name = data.get("wata_method")
    method = WATA_METHODS.get(method_name)

    if not method or not _wata_method_enabled(method):
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

        min_amount = WATA_MIN_AMOUNTS.get(method_name, 10)
        if currency == "USD":
            min_amount = 1
        currency_symbol = "$" if currency == "USD" else "₽"

        if user_amount < min_amount:
            await edit_or_send_message(
                target_message=message,
                text=f"❌ Минимальная сумма для оплаты через WATA — {currency_symbol}{min_amount}.",
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
    payment_url = await generate_wata_payment_link(amount_rub, message.chat.id, method, session)

    if not payment_url:
        await edit_or_send_message(
            target_message=message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")

    tg_id = message.from_user.id
    amount_text = await format_for_user(
        session, tg_id, float(amount_rub), language_code, force_currency=method["currency"]
    )

    await edit_or_send_message(
        target_message=message,
        text=WATA_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )

    await state.set_state(ReplenishBalanceWata.waiting_for_payment_confirmation)


@router.callback_query(F.data.startswith("wata_ru_amount|") | F.data.startswith("wata_int_amount|"))
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    amount = parse_amount_from_callback(callback_query.data, prefixes=["wata_ru", "wata_int"])
    if amount is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    method_name = "ru" if callback_query.data.startswith("wata_ru") else "int"
    method = WATA_METHODS.get(method_name)

    if not method or not _wata_method_enabled(method):
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    min_amount = WATA_MIN_AMOUNTS.get(method_name, 10)
    if amount < min_amount:
        currency_symbol = "$" if method["currency"] == "USD" else "₽"
        await edit_or_send_message(
            target_message=callback_query.message,
            text=f"❌ Минимальная сумма для оплаты через WATA — {currency_symbol}{min_amount}.",
            reply_markup=balance_fallback_kb(),
        )
        return

    await state.update_data(amount=amount)
    payment_url = await generate_wata_payment_link(amount, callback_query.message.chat.id, method, session)

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
    amount_text = await format_for_user(
        session, tg_id, float(amount), language_code, force_currency=method["currency"]
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=WATA_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )

    await state.set_state(ReplenishBalanceWata.waiting_for_payment_confirmation)


async def generate_wata_payment_link(
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
    token = method.get("token") or ""
    if not token:
        logger.error(f"[WATA] Не задан токен для кассы {method.get('currency')}")
        return None

    currency = str(method.get("currency") or "RUB").upper()
    unique_order_id = payment_id or f"{int(time.time())}_{tg_id}_{int(amount)}"

    pending_metadata = dict(metadata or {})
    pending_metadata.setdefault("cassa", "ru" if currency == "RUB" else "int")

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
            pending_metadata["wata_currency"] = currency
        except Exception as e:
            logger.error(f"[WATA] Не удалось сконвертировать {amount} RUB → {currency}: {e}")
            return None

    body = {
        "amount": api_amount,
        "currency": currency,
        "orderId": unique_order_id,
        "orderDescription": WATA_PAYMENT_TITLE,
        "successUrl": success_url or WATA_SUCCESS_URL or "",
        "failUrl": failure_url or WATA_FAIL_URL or "",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            async with http_session.post(WATA_API_LINKS_URL, headers=headers, json=body) as resp:
                if resp.status != 200:
                    try:
                        error_json = await resp.json()
                        logger.error(f"[WATA] API error: status={resp.status}, response={error_json}")
                    except Exception:
                        text = await resp.text()
                        logger.error(f"[WATA] API error: status={resp.status}, non-JSON: {text[:300]}")
                    return None

                try:
                    resp_json = await resp.json()
                except Exception as e:
                    text = await resp.text()
                    logger.error(f"[WATA] Не удалось распарсить JSON: {e}, ответ={text[:300]}")
                    return None

                payment_url = resp_json.get("url")
                if not payment_url:
                    logger.error(f"[WATA] В ответе нет поля url: {resp_json}")
                    return None

                async with async_session_maker() as db_session:
                    try:
                        await add_payment(
                            session=db_session,
                            tg_id=tg_id,
                            amount=float(int(amount)),
                            payment_system="wata",
                            status="pending",
                            currency="RUB",
                            payment_id=unique_order_id,
                            metadata=pending_metadata,
                            original_amount=pending_original_amount,
                        )
                        await db_session.commit()
                    except Exception as e:
                        logger.error(
                            f"[WATA] Не удалось записать pending платёж в БД "
                            f"(order_id={unique_order_id}, tg_id={tg_id}): {e}"
                        )
                        await db_session.rollback()
                        return None
                logger.info(
                    f"[WATA] Ссылка создана: tg_id={tg_id}, order_id={unique_order_id}, "
                    f"rub_amount={amount}, api_amount={api_amount} {currency}"
                )
                return payment_url
    except Exception as e:
        logger.error(f"[WATA] Ошибка создания платежа: {e}")
        return None


def create_link_factory(method_name: str):
    async def create_link(
        session: AsyncSession,
        tg_id: int,
        amount: float,
        currency: str,
        success_url: str | None,
        failure_url: str | None,
        metadata: dict | None,
    ) -> tuple[str, str | None]:
        method = WATA_METHODS.get(method_name)
        if not method or not _wata_method_enabled(method):
            raise ValueError("Способ оплаты Wata недоступен")

        amount_int = int(amount)
        if amount_int <= 0:
            raise ValueError("Сумма должна быть больше нуля")

        min_amount = WATA_MIN_AMOUNTS.get(method_name, 10)
        if amount_int < min_amount:
            symbol = "$" if method["currency"] == "USD" else "₽"
            raise ValueError(f"Минимальная сумма Wata — {symbol}{min_amount}")

        payment_id = f"{int(time.time())}_{tg_id}_{amount_int}"
        url = await generate_wata_payment_link(
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
            raise ValueError("Не удалось создать платёж Wata")
        return (url, payment_id)

    return create_link


register_payment_creator("WATA_RU", create_link_factory("ru"))
register_payment_creator("WATA_INT", create_link_factory("int"))
