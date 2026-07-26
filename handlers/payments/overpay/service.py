import asyncio
import os
import ssl
import tempfile
import time

import aiohttp

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    OVERPAY_API_URL,
    OVERPAY_CARDS_TERMINAL_ID,
    OVERPAY_CERT_PASSWORD,
    OVERPAY_CERT_PATH,
    OVERPAY_LIVETIME_MINUTES,
    OVERPAY_PASSWORD,
    OVERPAY_RETURN_URL,
    OVERPAY_SBP_DIRECT_QR,
    OVERPAY_SBP_TERMINAL_ID,
    OVERPAY_SERVER_IP,
    OVERPAY_USERNAME,
)
from core.bootstrap import PAYMENTS_CONFIG
from database import register_pending_payment
from database.models import User
from handlers.buttons import BACK, OVERPAY_CARDS, OVERPAY_SBP, PAY_2
from handlers.payments.keyboards import (
    balance_fallback_kb,
    build_amounts_keyboard,
    parse_amount_from_callback,
    pay_keyboard,
    payment_options_for_user,
)
from handlers.texts import (
    OVERPAY_CARDS_DESCRIPTION,
    OVERPAY_PAYMENT_MESSAGE,
    OVERPAY_PAYMENT_TITLE,
    OVERPAY_SBP_DESCRIPTION,
)
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.currency_rates import format_for_user
from services.payments.payment_links import register_payment_creator


router = Router()

OVERPAY_MIN_AMOUNT = 10

_ssl_context: ssl.SSLContext | None = None


class ReplenishBalanceOverpay(StatesGroup):
    choosing_amount = State()
    waiting_for_payment_confirmation = State()
    entering_custom_amount = State()


OVERPAY_METHODS: dict[str, dict] = {
    "cards": {
        "provider_key": "OVERPAY_CARDS",
        "payment_method": "card",
        "terminal_id": OVERPAY_CARDS_TERMINAL_ID,
        "button": OVERPAY_CARDS,
        "desc": OVERPAY_CARDS_DESCRIPTION,
    },
    "sbp": {
        "provider_key": "OVERPAY_SBP",
        "payment_method": "fps",
        "terminal_id": OVERPAY_SBP_TERMINAL_ID,
        "button": OVERPAY_SBP,
        "desc": OVERPAY_SBP_DESCRIPTION,
    },
}


def _overpay_method_enabled(method: dict) -> bool:
    if not bool(PAYMENTS_CONFIG.get(method["provider_key"], False)):
        return False
    return bool(str(method.get("terminal_id") or "").strip())


def _overpay_credentials_ok() -> bool:
    return (
        bool((OVERPAY_API_URL or "").strip())
        and bool((OVERPAY_USERNAME or "").strip())
        and bool((OVERPAY_PASSWORD or "").strip())
        and bool((OVERPAY_CERT_PATH or "").strip())
    )


def _resolve_cert_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(base_dir, "..", "..", ".."))
    return os.path.join(project_root, path)


def _build_ssl_context() -> ssl.SSLContext | None:
    global _ssl_context
    if _ssl_context is not None:
        return _ssl_context

    cert_path = (OVERPAY_CERT_PATH or "").strip()
    if not cert_path:
        logger.error("[Overpay] OVERPAY_CERT_PATH не задан")
        return None

    resolved = _resolve_cert_path(cert_path)
    if not os.path.isfile(resolved):
        logger.error(f"[Overpay] Файл сертификата не найден: {resolved}")
        return None

    try:
        with open(resolved, "rb") as cert_file:
            p12_data = cert_file.read()

        password = (OVERPAY_CERT_PASSWORD or "").encode("utf-8") or None
        private_key, certificate, additional = pkcs12.load_key_and_certificates(p12_data, password)
        if private_key is None or certificate is None:
            logger.error("[Overpay] В .p12 нет приватного ключа или сертификата")
            return None

        pem_bundle = certificate.public_bytes(Encoding.PEM)
        pem_bundle += private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        for ca_cert in additional or []:
            pem_bundle += ca_cert.public_bytes(Encoding.PEM)

        context = ssl.create_default_context()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
        try:
            tmp.write(pem_bundle)
            tmp.flush()
            tmp.close()
            context.load_cert_chain(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        _ssl_context = context
        logger.info("[Overpay] SSL-контекст с клиентским сертификатом успешно построен")
        return context
    except Exception as e:
        logger.error(f"[Overpay] Не удалось загрузить сертификат .p12: {e}")
        return None


def _base_url() -> str:
    return (OVERPAY_API_URL or "").rstrip("/")


async def _get_user_language(session: AsyncSession, tg_id: int) -> str | None:
    result = await session.execute(select(User.language_code).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


async def process_callback_pay_overpay(
    callback_query: types.CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    method_name: str,
):
    try:
        tg_id = callback_query.from_user.id
        await state.clear()

        method = OVERPAY_METHODS.get(method_name)
        if not method or not _overpay_method_enabled(method):
            await edit_or_send_message(
                target_message=callback_query.message,
                text="Ошибка: выбранный способ оплаты недоступен.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
            return

        if not _overpay_credentials_ok():
            logger.error("[Overpay] Не заданы реквизиты API (URL/логин/пароль/сертификат)")
            await edit_or_send_message(
                target_message=callback_query.message,
                text="Ошибка: платежная система временно недоступна.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
            )
            return

        language_code = await _get_user_language(session, tg_id)
        opts = await payment_options_for_user(session, tg_id, language_code, force_currency="RUB")
        builder = build_amounts_keyboard(
            prefix=f"overpay_{method_name}",
            pattern="{prefix}_amount|{price}",
            back_cb="balance",
            custom_cb=f"overpay_custom_amount|{method_name}",
            opts=opts,
        )

        await edit_or_send_message(
            target_message=callback_query.message,
            text=method["desc"],
            reply_markup=builder,
        )
        await state.update_data(
            overpay_method=method_name,
            message_id=callback_query.message.message_id,
            chat_id=callback_query.message.chat.id,
        )
        await state.set_state(ReplenishBalanceOverpay.choosing_amount)
    except Exception as e:
        logger.error(f"[Overpay] Ошибка в process_callback_pay_overpay для {callback_query.from_user.id}: {e}")
        await callback_query.answer(
            "Произошла ошибка при инициализации платежа. Попробуйте позже.",
            show_alert=True,
        )


@router.callback_query(F.data == "pay_overpay_cards")
async def _pay_overpay_cards(cb: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_overpay(cb, state, session, "cards")


@router.callback_query(F.data == "pay_overpay_sbp")
async def _pay_overpay_sbp(cb: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_overpay(cb, state, session, "sbp")


@router.callback_query(F.data.startswith("overpay_custom_amount|"))
async def process_custom_amount_button(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    method_name = callback_query.data.split("|")[1]
    method = OVERPAY_METHODS.get(method_name)
    if not method:
        return

    await state.update_data(overpay_method=method_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"pay_overpay_{method_name}"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text="Пожалуйста, введите сумму пополнения в рублях (₽).",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(ReplenishBalanceOverpay.entering_custom_amount)


@router.message(ReplenishBalanceOverpay.entering_custom_amount)
async def handle_custom_amount_input(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    method_name = data.get("overpay_method")
    method = OVERPAY_METHODS.get(method_name)

    if not method or not _overpay_method_enabled(method) or not _overpay_credentials_ok():
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
        if user_amount < OVERPAY_MIN_AMOUNT:
            await edit_or_send_message(
                target_message=message,
                text=f"❌ Минимальная сумма для оплаты — {OVERPAY_MIN_AMOUNT}₽.",
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
    result = await generate_overpay_payment_link(user_amount, message.from_user.id, method, session)
    if not result:
        await edit_or_send_message(
            target_message=message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    payment_url = result[0]
    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")
    tg_id = message.from_user.id
    language_code = await _get_user_language(session, tg_id)
    amount_text = await format_for_user(session, tg_id, float(user_amount), language_code, force_currency="RUB")
    await edit_or_send_message(
        target_message=message,
        text=OVERPAY_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )
    await state.set_state(ReplenishBalanceOverpay.waiting_for_payment_confirmation)


@router.callback_query(
    F.data.startswith("overpay_cards_amount|") | F.data.startswith("overpay_sbp_amount|")
)
async def process_amount_selection(callback_query: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    prefixes = ["overpay_cards", "overpay_sbp"]
    amount = parse_amount_from_callback(callback_query.data, prefixes=prefixes)
    if amount is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Некорректная сумма.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    method_name = next(
        (p.removeprefix("overpay_") for p in prefixes if callback_query.data.startswith(f"{p}_amount|")),
        None,
    )
    method = OVERPAY_METHODS.get(method_name) if method_name else None

    if not method or not _overpay_method_enabled(method) or not _overpay_credentials_ok():
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Ошибка: выбранный способ оплаты недоступен.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    if amount < OVERPAY_MIN_AMOUNT:
        await edit_or_send_message(
            target_message=callback_query.message,
            text=f"❌ Минимальная сумма для оплаты — {OVERPAY_MIN_AMOUNT}₽.",
            reply_markup=balance_fallback_kb(),
        )
        return

    await state.update_data(amount=amount)
    result = await generate_overpay_payment_link(amount, callback_query.from_user.id, method, session)
    if not result:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Произошла ошибка при создании платежа. Попробуйте позже или выберите другой способ оплаты.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
        )
        return

    payment_url = result[0]
    confirm_keyboard = pay_keyboard(payment_url, pay_text=PAY_2, back_cb="balance")
    tg_id = callback_query.from_user.id
    language_code = await _get_user_language(session, tg_id)
    amount_text = await format_for_user(session, tg_id, float(amount), language_code, force_currency="RUB")
    await edit_or_send_message(
        target_message=callback_query.message,
        text=OVERPAY_PAYMENT_MESSAGE.format(amount=amount_text),
        reply_markup=confirm_keyboard,
    )
    await state.set_state(ReplenishBalanceOverpay.waiting_for_payment_confirmation)


async def _create_via_preflight(
    amount: int,
    terminal_id: str,
    payment_method: str,
    unique_tx_id: str,
    ret_url: str,
    context: ssl.SSLContext,
) -> tuple[str, str] | None:
    body: dict = {
        "amount": f"{int(amount):.2f}",
        "currency": "RUB",
        "livetimeMinutes": int(OVERPAY_LIVETIME_MINUTES or 60),
        "projectId": terminal_id,
        "paymentMethods": [payment_method],
        "description": OVERPAY_PAYMENT_TITLE,
        "merchantTransactionId": unique_tx_id,
    }
    if ret_url:
        body["returnUrl"] = ret_url

    url = f"{_base_url()}/orders/preflight"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    auth = aiohttp.BasicAuth(OVERPAY_USERNAME, OVERPAY_PASSWORD)
    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    connector = aiohttp.TCPConnector(ssl=context)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, auth=auth) as http_session:
        async with http_session.post(url, headers=headers, json=body) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                logger.error(f"[Overpay] API error (preflight): status={resp.status}, body={text[:500]}")
                return None
            try:
                resp_json = await resp.json(content_type=None)
            except Exception as e:
                text = await resp.text()
                logger.error(f"[Overpay] Невалидный JSON в ответе preflight ({e}): {text[:300]}")
                return None

            order_id = str(resp_json.get("id") or "")
            payment_url = resp_json.get("resultUrl")
            if not order_id or not payment_url:
                logger.error(f"[Overpay] В ответе preflight нет id или resultUrl: {resp_json}")
                return None
            return (payment_url, order_id)


async def _create_via_sbp_direct(
    amount: int,
    tg_id: int,
    terminal_id: str,
    unique_tx_id: str,
    ret_url: str,
    context: ssl.SSLContext,
) -> tuple[str, str] | None:
    server_ip = (OVERPAY_SERVER_IP or "").strip()
    if not server_ip:
        logger.error("[Overpay] OVERPAY_SERVER_IP не задан — обязателен для прямого QR СБП (/api/orders/init)")
        return None
    if not ret_url:
        logger.error("[Overpay] Не задан returnUrl — обязателен для прямого QR СБП")
        return None

    init_body: dict = {
        "amount": f"{int(amount):.2f}",
        "currency": "RUB",
        "paymentMethod": "fps",
        "type": "PURCHASE",
        "projectId": terminal_id,
        "description": OVERPAY_PAYMENT_TITLE,
        "merchantTransactionId": unique_tx_id,
        "location": {"ip": server_ip},
        "client": {"email": f"{tg_id}@example.com"},
        "options": {"returnUrl": ret_url, "secure3d20ReturnUrl": ret_url},
    }

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    auth = aiohttp.BasicAuth(OVERPAY_USERNAME, OVERPAY_PASSWORD)
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    connector = aiohttp.TCPConnector(ssl=context)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, auth=auth) as http_session:
        async with http_session.post(f"{_base_url()}/api/orders/init", headers=headers, json=init_body) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                logger.error(f"[Overpay] SBP init error: status={resp.status}, body={text[:500]}")
                return None
            try:
                init_json = await resp.json(content_type=None)
            except Exception as e:
                text = await resp.text()
                logger.error(f"[Overpay] Невалидный JSON в ответе init ({e}): {text[:300]}")
                return None

        order_id = str(init_json.get("id") or "")
        if not order_id:
            logger.error(f"[Overpay] SBP init: нет id в ответе: {init_json}")
            return None
        logger.info(f"[Overpay] SBP init ok: id={order_id}, status={init_json.get('status')}")

        payment_url: str | None = None
        last_status: str | None = None
        for _ in range(10):
            async with http_session.get(
                f"{_base_url()}/orders/{order_id}", headers={"Accept": "application/json"}
            ) as r:
                if r.status == 200:
                    try:
                        get_json = await r.json(content_type=None)
                    except Exception:
                        get_json = {}
                    orders = get_json.get("orders") or []
                    if orders:
                        order = orders[0]
                        last_status = str(order.get("status") or "")
                        interaction = order.get("interaction") or {}
                        payment_url = interaction.get("redirectLink")
                        if payment_url:
                            break
                        if last_status.lower() in ("error", "declined", "rejected", "failed"):
                            break
                else:
                    logger.warning(f"[Overpay] SBP get {r.status}: {(await r.text())[:200]}")
            await asyncio.sleep(0.5)

    if not payment_url:
        logger.error(
            f"[Overpay] SBP: не получена interaction.redirectLink для заказа {order_id} (last_status={last_status})"
        )
        return None
    return (payment_url, order_id)


async def generate_overpay_payment_link(
    amount: int,
    tg_id: int,
    method: dict,
    session: AsyncSession | None = None,
    *,
    merchant_tx_id: str | None = None,
    return_url: str | None = None,
    metadata: dict | None = None,
) -> tuple[str, str] | None:
    if not _overpay_credentials_ok():
        logger.error("[Overpay] Не заданы реквизиты API")
        return None

    context = _build_ssl_context()
    if context is None:
        return None

    method_name = next((k for k, v in OVERPAY_METHODS.items() if v is method), None) or ""
    payment_method = str(method.get("payment_method") or "card")
    terminal_id = str(method.get("terminal_id") or "").strip()
    if not terminal_id:
        logger.error(f"[Overpay] Не задан terminal_id для метода '{method_name}'")
        return None
    unique_tx_id = merchant_tx_id or f"ovp_{int(time.time())}_{tg_id}"
    ret_url = return_url or OVERPAY_RETURN_URL or ""

    use_sbp_direct = method_name == "sbp" and bool(OVERPAY_SBP_DIRECT_QR)

    try:
        if use_sbp_direct:
            created = await _create_via_sbp_direct(amount, tg_id, terminal_id, unique_tx_id, ret_url, context)
        else:
            created = await _create_via_preflight(amount, terminal_id, payment_method, unique_tx_id, ret_url, context)
    except Exception as e:
        logger.error(f"[Overpay] Ошибка создания платежа: {e}")
        return None

    if not created:
        return None
    payment_url, order_id = created

    pending_metadata = dict(metadata or {})
    pending_metadata.setdefault("provider", "overpay")
    pending_metadata["overpay_method"] = method_name
    pending_metadata["overpay_payment_method"] = payment_method
    pending_metadata["overpay_merchant_tx_id"] = unique_tx_id
    pending_metadata["overpay_order_id"] = order_id
    pending_metadata["overpay_sbp_direct"] = use_sbp_direct

    await register_pending_payment(
        payment_id=order_id,
        tg_id=tg_id,
        amount=float(int(amount)),
        payment_system="overpay",
        currency="RUB",
        metadata=pending_metadata,
    )
    logger.info(
        f"[Overpay] Ссылка создана: tg_id={tg_id}, order_id={order_id}, method={payment_method}, "
        f"direct_qr={use_sbp_direct}, merchant_tx_id={unique_tx_id}, amount={amount} RUB"
    )
    return (payment_url, order_id)


async def get_overpay_order(order_id: str) -> dict | None:
    if not _overpay_credentials_ok():
        return None

    context = _build_ssl_context()
    if context is None:
        return None

    url = f"{_base_url()}/orders/{order_id}"
    headers = {"Accept": "application/json"}
    auth = aiohttp.BasicAuth(OVERPAY_USERNAME, OVERPAY_PASSWORD)
    timeout = aiohttp.ClientTimeout(total=30, connect=10)

    try:
        connector = aiohttp.TCPConnector(ssl=context)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector, auth=auth) as http_session:
            async with http_session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"[Overpay] Не удалось получить заказ {order_id}: status={resp.status}, body={text[:300]}")
                    return None
                resp_json = await resp.json(content_type=None)
                orders = resp_json.get("orders")
                if isinstance(orders, list) and orders:
                    return orders[0]
                if isinstance(resp_json, dict) and resp_json.get("id"):
                    return resp_json
                logger.error(f"[Overpay] Пустой ответ по заказу {order_id}: {resp_json}")
                return None
    except Exception as e:
        logger.error(f"[Overpay] Ошибка получения заказа {order_id}: {e}")
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
            raise ValueError("Overpay поддерживает только RUB")
        method = OVERPAY_METHODS.get(method_name)
        if not method or not _overpay_method_enabled(method):
            raise ValueError("Способ оплаты Overpay недоступен")

        amount_int = int(amount)
        if amount_int < OVERPAY_MIN_AMOUNT:
            raise ValueError(f"Минимальная сумма — {OVERPAY_MIN_AMOUNT}₽")

        result = await generate_overpay_payment_link(
            amount_int,
            tg_id,
            method,
            session,
            return_url=success_url,
            metadata=metadata,
        )
        if not result:
            raise ValueError("Не удалось создать платеж Overpay")
        payment_url, order_id = result
        return (payment_url, order_id)

    return create_link


for _name in OVERPAY_METHODS:
    register_payment_creator(OVERPAY_METHODS[_name]["provider_key"], _create_link_factory(_name))
