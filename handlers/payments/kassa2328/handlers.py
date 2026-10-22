import json

from datetime import datetime
from decimal import ROUND_UP, Decimal
from html import escape as html_escape
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import bot
from core.settings import kassa2328_config
from database.access.resolution import TelegramId, UserId, notify_telegram_chat_id
from database.kassa2328 import load_order
from database.keys import get_key_by_email, get_key_by_server
from database.payments import resolve_payment_creation_owner
from database.temporary_data import get_temporary_data
from handlers.keys.view.payload import build_key_view_message, send_key_info
from handlers.notifications.webapp_only import webapp_only_markup
from handlers.payments.constants import ALLOWED_TEMP_PAYMENT_STATES
from handlers.payments.keyboards import (
    back_keyboard,
    build_amounts_keyboard,
    parse_amount_from_callback,
    payment_options_for_user,
)
from handlers.utils import edit_or_send_message
from logger import logger
from services.gifts import format_gift_limits_display
from services.payments.currency_rates import to_rub
from services.payments.kassa2328.api import positive_decimal, rub_amount
from services.payments.kassa2328.service import (
    available,
    create_invoice,
    register_checkout_notifier,
)
from settings import buttons, texts


router = Router(name="kassa2328_core")

NON_REUSABLE_STATUSES = {"cancel", "refund_process", "refund_fail", "refund_paid", "underpaid", "aml_lock"}


class Replenish2328State(StatesGroup):
    entering_custom_amount = State()


def _invoice_currency() -> str:
    """Возвращает валюту счёта для отображения и ввода."""
    return str(kassa2328_config.KASSA2328_INVOICE_CURRENCY).strip().upper()


def _amount_error() -> str:
    """Формирует подсказку о допустимой сумме пополнения."""
    return texts.KASSA2328_AMOUNT_ERROR.format(
        min_amount=kassa2328_config.KASSA2328_MIN_AMOUNT_RUB,
        max_amount=kassa2328_config.KASSA2328_MAX_AMOUNT_RUB,
    )


async def _available_for(event: Message | CallbackQuery) -> bool:
    """Проверяет доступность создания новых счетов."""
    if available():
        return True
    if isinstance(event, CallbackQuery):
        await event.answer(texts.KASSA2328_UNAVAILABLE, show_alert=True)
    else:
        await event.answer(texts.KASSA2328_UNAVAILABLE, reply_markup=back_keyboard("pay"))
    return False


async def _invoice_context_current(state: FSMContext | None, request_id: str, expected_state: str | None) -> bool:
    """Не позволяет старому запросу заменить новый экран оплаты."""
    if state is None:
        return True
    return (await state.get_data()).get(
        "kassa2328_request_id"
    ) == request_id and await state.get_state() == expected_state


async def _state_order_id(state: FSMContext, user_id: UserId, amount: Decimal) -> str:
    """Сохраняет номер заказа до обращения к платёжной кассе."""
    data = await state.get_data()
    previous_id = data.get("kassa2328_order_id")
    if isinstance(previous_id, str) and previous_id.startswith("k2328_"):
        previous = await load_order(previous_id, user_id)
        if previous is not None:
            metadata = previous.get("metadata") or {}
            request = json.loads(previous["request_json"])
            if (
                Decimal(str(previous["amount_rub"])) == amount
                and not previous["credited"]
                and previous.get("provider_status") not in NON_REUSABLE_STATUSES
                and metadata.get("source") == "telegram_balance"
                and metadata.get("payment_flow") == "balance_topup"
                and metadata.get("kassa2328_fingerprint")
                and request.get("currency") == _invoice_currency()
                and request.get("url_callback") == kassa2328_config.KASSA2328_CALLBACK_URL
                and request.get("url_return", "") == kassa2328_config.KASSA2328_RETURN_URL
            ):
                return previous_id
        elif data.get("kassa2328_order_amount_rub") == str(amount) and data.get("kassa2328_order_user_id") == int(
            user_id
        ):
            return previous_id
    order_id = "k2328_" + uuid4().hex
    await state.update_data(
        kassa2328_order_id=order_id,
        kassa2328_order_amount_rub=str(amount),
        kassa2328_order_user_id=int(user_id),
    )
    return order_id


async def _show_invoice(
    event: Message | CallbackQuery,
    session: AsyncSession,
    amount: Decimal,
    *,
    state: FSMContext | None = None,
    back: str = "pay",
    fast: bool = False,
    metadata: dict | None = None,
    target_message: Message | None = None,
) -> bool:
    """Создаёт счёт и обновляет экран оплаты."""
    if not await _available_for(event):
        return False
    message = (
        target_message if target_message is not None else event.message if isinstance(event, CallbackQuery) else event
    )
    if not isinstance(message, Message):
        return False
    order_id = None
    expected_state = await state.get_state() if state is not None else None
    request_id = uuid4().hex
    if state is not None:
        await state.update_data(kassa2328_request_id=request_id)
    try:
        user_id = await resolve_payment_creation_owner(session, TelegramId(event.from_user.id))
        if state is not None:
            order_id = await _state_order_id(state, user_id, amount)
        order, response = await create_invoice(
            session,
            user_id,
            amount,
            order_id=order_id,
            metadata=metadata or {"source": "telegram_balance", "payment_flow": "balance_topup"},
            trusted_legacy=fast,
        )
        if not await _invoice_context_current(state, request_id, expected_state):
            return False
        if state is not None:
            await state.update_data(kassa2328_order_id=order["order_id"])
            await state.set_state(None)
            expected_state = None
        request = json.loads(order["request_json"])
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(text=buttons.PAY_2, url=response["url"]))
        keyboard.row(
            InlineKeyboardButton(
                text=buttons.MAIN_MENU if fast else buttons.KASSA2328_DONE,
                callback_data="profile" if fast else "balance",
            )
        )
        keyboard.row(InlineKeyboardButton(text=buttons.BACK, callback_data=back))
        await edit_or_send_message(
            target_message=message,
            text=texts.KASSA2328_INVOICE_CREATED.format(
                amount_rub=order["amount_rub"],
                amount=html_escape(str(request["amount"])),
                currency=html_escape(str(request["currency"])),
            ),
            reply_markup=keyboard.as_markup(),
        )
        return True
    except Exception as exc:
        logger.error("[2328] Не удалось создать счёт: {}", type(exc).__name__)
        if not await _invoice_context_current(state, request_id, expected_state):
            return False
        await edit_or_send_message(
            target_message=message,
            text=texts.KASSA2328_CREATE_LINK_ERROR,
            reply_markup=back_keyboard(back),
        )
        return False


@router.callback_query(F.data.in_({"pay_2328", "k2328_back"}))
async def pay_2328_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Открывает выбор суммы пополнения через 2328."""
    if not await _available_for(callback):
        return
    await callback.answer()
    await state.set_state(None)
    request_id = uuid4().hex
    await state.update_data(kassa2328_request_id=request_id)
    options = await payment_options_for_user(
        session,
        TelegramId(callback.from_user.id),
        callback.from_user.language_code,
        force_currency=_invoice_currency(),
    )
    if not await _invoice_context_current(state, request_id, None):
        return
    await edit_or_send_message(
        target_message=callback.message,
        text=texts.KASSA2328_PAYMENT_MENU_TITLE,
        reply_markup=build_amounts_keyboard(
            prefix="k2328",
            pattern="{prefix}_amount|{price}",
            back_cb="pay",
            custom_cb="k2328_custom",
            opts=options,
        ),
    )


@router.callback_query(F.data.startswith("k2328_amount|"))
async def pay_2328_amount(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Создаёт счёт для выбранной рублёвой суммы."""
    if not await _available_for(callback):
        return
    try:
        amount = rub_amount(parse_amount_from_callback(callback.data, prefixes=["k2328"]))
    except (ValueError, ArithmeticError, TypeError):
        await callback.answer(texts.KASSA2328_INVALID_AMOUNT, show_alert=True)
        return
    await callback.answer()
    await state.set_state(None)
    await _show_invoice(callback, session, amount, state=state)


@router.callback_query(F.data == "k2328_custom")
async def custom_amount(callback: CallbackQuery, state: FSMContext) -> None:
    """Предлагает ввести сумму в валюте счёта."""
    if not await _available_for(callback):
        return
    currency = _invoice_currency()
    await callback.answer()
    await state.set_state(Replenish2328State.entering_custom_amount)
    await state.update_data(
        kassa2328_input_currency=currency,
        kassa2328_request_id=uuid4().hex,
        kassa2328_prompt={
            "message_id": callback.message.message_id,
            "chat_id": callback.message.chat.id,
            "has_caption": getattr(callback.message, "content_type", None)
            in {"photo", "video", "animation", "audio", "document", "voice"},
        },
    )
    await edit_or_send_message(
        target_message=callback.message,
        text=texts.KASSA2328_ENTER_AMOUNT.format(currency=currency),
        reply_markup=back_keyboard("pay_2328"),
    )


@router.message(Replenish2328State.entering_custom_amount)
async def custom_amount_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Переводит введённую сумму в рубли и создаёт счёт."""
    if not await _available_for(message):
        return
    expected_state = Replenish2328State.entering_custom_amount.state
    if await state.get_state() != expected_state:
        return
    request_id = uuid4().hex
    await state.update_data(kassa2328_request_id=request_id)
    prompt = (await state.get_data()).get("kassa2328_prompt")
    target_message = None
    if (
        isinstance(prompt, dict)
        and prompt.get("chat_id") == message.chat.id
        and type(prompt.get("message_id")) is int
        and prompt["message_id"] > 0
    ):
        target_message = Message(
            message_id=prompt["message_id"],
            date=message.date,
            chat=message.chat,
            message_thread_id=message.message_thread_id,
            business_connection_id=message.business_connection_id,
            caption="" if prompt.get("has_caption") else None,
        ).as_(message.bot)
    try:
        entered = positive_decimal((message.text or "").strip().replace(",", "."))
        if entered != entered.quantize(Decimal("0.01")):
            raise ValueError("Invalid amount precision")
        currency = (await state.get_data()).get("kassa2328_input_currency", _invoice_currency())
        if currency not in {"RUB", "USD"}:
            raise ValueError("Invalid input currency")
        amount = entered if currency == "RUB" else await to_rub(entered, currency, session=None)
        amount = rub_amount(amount.quantize(Decimal("0.01"), rounding=ROUND_UP))
    except (ValueError, ArithmeticError, TypeError):
        if not await _invoice_context_current(state, request_id, expected_state):
            return
        await edit_or_send_message(target_message or message, _amount_error(), reply_markup=back_keyboard("pay_2328"))
        return
    except Exception as exc:
        logger.error("[2328] Не удалось пересчитать сумму: {}", type(exc).__name__)
        if not await _invoice_context_current(state, request_id, expected_state):
            return
        await edit_or_send_message(
            target_message or message, texts.KASSA2328_RATE_ERROR, reply_markup=back_keyboard("pay_2328")
        )
        return
    if not await _invoice_context_current(state, request_id, expected_state):
        return
    await _show_invoice(message, session, amount, state=state, target_message=target_message)


async def handle_custom_amount_input_kassa2328(event: CallbackQuery, session: AsyncSession) -> None:
    """Создаёт счёт 2328 для сохранённого оформления покупки."""
    if not await _available_for(event):
        return
    temporary = await get_temporary_data(session, TelegramId(event.from_user.id))
    if (
        not temporary
        or temporary.get("state") not in ALLOWED_TEMP_PAYMENT_STATES
        or not isinstance(temporary.get("data"), dict)
    ):
        await edit_or_send_message(
            target_message=event.message,
            text=texts.KASSA2328_CHECKOUT_MISSING,
            reply_markup=back_keyboard("fastflow_back"),
        )
        return
    try:
        amount = rub_amount(temporary["data"].get("required_amount"))
    except (ValueError, ArithmeticError, TypeError):
        await edit_or_send_message(
            target_message=event.message,
            text=texts.KASSA2328_AMOUNT_UNAVAILABLE,
            reply_markup=back_keyboard("fastflow_back"),
        )
        return
    await _show_invoice(
        event,
        session,
        amount,
        back="fastflow_back",
        fast=True,
        metadata={
            "source": "telegram_fast_flow",
            "checkout_state": temporary["state"],
            "checkout_data": dict(temporary["data"]),
        },
    )


async def notify_checkout_completed(session: AsyncSession, owner: int, intent: dict, result: dict) -> None:
    """Показывает подписку или ссылку уже оплаченного подарка."""
    data = intent.get("data") or {}
    if (
        result.get("status") != "applied"
        or intent.get("state") == "balance_topup"
        or data.get("payment_flow") == "balance_topup"
    ):
        return
    owner_id = UserId(owner)
    chat_id = await notify_telegram_chat_id(session, owner_id)
    if chat_id is None:
        return
    gift = result.get("gift")
    if isinstance(gift, dict):
        limits = await format_gift_limits_display(
            session,
            tariff_id=data.get("tariff_id"),
            selected_device_limit=data.get("selected_device_limit"),
            selected_traffic_gb=data.get("selected_traffic_limit_gb"),
        )
        expiry_time = datetime.fromisoformat(gift["expiry_time"]).strftime("%d-%m-%Y %H:%M")
        text = texts.GIFT_ITEM_TEMPLATE.format(
            months=html_escape(str(gift["duration_text"])),
            tariff_name=html_escape(str(gift["tariff_name"])),
            expiry_time=expiry_time,
            recipient=texts.KASSA2328_GIFT_RECIPIENT_PENDING,
            limits=limits,
            is_used=texts.KASSA2328_GIFT_UNUSED,
            gift_link=html_escape(str(gift["gift_link"])),
            site_gift_link=html_escape(str(gift["site_gift_link"])),
        )
        keyboard = InlineKeyboardBuilder()
        keyboard.row(InlineKeyboardButton(text=buttons.MY_GIFTS, callback_data="my_gifts"))
        keyboard.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=webapp_only_markup() or keyboard.as_markup(),
        )
        return
    client_id = result.get("client_id")
    key = await get_key_by_server(session, owner_id, str(client_id)) if client_id else None
    if key is None and data.get("created_email"):
        key = await get_key_by_email(session, str(data["created_email"]), owner_id)
    if key is None:
        logger.warning("[2328] Подписка оплаченного заказа не найдена у владельца {}", int(owner_id))
        return
    if intent.get("state") == "waiting_for_addons_payment":
        await send_key_info(bot, session, owner_id, key.email)
        return
    text, keyboard = await build_key_view_message(session=session, email=key.email)
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=webapp_only_markup() or keyboard)


register_checkout_notifier(notify_checkout_completed)
