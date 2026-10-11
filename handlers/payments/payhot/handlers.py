from copy import deepcopy
from decimal import Decimal
from html import escape as html_escape
from uuid import uuid4

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import TelegramId
from database.temporary_data import get_temporary_data
from handlers.payments.constants import ALLOWED_TEMP_PAYMENT_STATES
from handlers.payments.keyboards import back_keyboard, build_amounts_keyboard, pay_keyboard, payment_options_for_user
from handlers.utils import edit_or_send_message
from logger import logger
from services.payments.currency_rates import format_for_user
from services.payments.payhot.api import normalize_phone, rub_amount
from services.payments.payhot.service import available, create_invoice
from settings import buttons, texts


router = Router(name="payhot_core")
METHOD_LABELS = {
    "cards": texts.PAYHOT_METHOD_CARDS,
    "sbp": texts.PAYHOT_METHOD_SBP,
    "sberpay": texts.PAYHOT_METHOD_SBERPAY,
}


class ReplenishPayhotState(StatesGroup):
    entering_custom_amount = State()
    entering_phone = State()


async def _available_for(
    event: Message | CallbackQuery,
    method_name: str,
    *,
    target_message: Message | None = None,
    back: str = "pay",
) -> bool:
    """Проверяет доступность выбранного способа оплаты."""
    if method_name in METHOD_LABELS and available(method_name):
        return True
    if isinstance(event, CallbackQuery):
        await event.answer(texts.PAYHOT_UNAVAILABLE, show_alert=True)
    else:
        await edit_or_send_message(
            target_message=target_message or event,
            text=texts.PAYHOT_UNAVAILABLE,
            reply_markup=back_keyboard(back),
        )
    return False


async def _invoice_context_current(state: FSMContext | None, request_id: str, expected_state: str | None) -> bool:
    """Защищает новый экран от устаревшего запроса."""
    if state is None:
        return True
    return (await state.get_data()).get("payhot_request_id") == request_id and await state.get_state() == expected_state


def _saved_prompt(message: Message, data: dict) -> Message | None:
    """Восстанавливает сообщение бота для редактирования."""
    prompt = data.get("payhot_prompt")
    if (
        not isinstance(prompt, dict)
        or prompt.get("chat_id") != message.chat.id
        or type(prompt.get("message_id")) is not int
        or prompt["message_id"] <= 0
    ):
        return None
    return Message(
        message_id=prompt["message_id"],
        date=message.date,
        chat=message.chat,
        message_thread_id=message.message_thread_id,
        business_connection_id=message.business_connection_id,
        caption="" if prompt.get("has_caption") else None,
    ).as_(message.bot)


def _prompt_details(message: Message) -> dict:
    """Сохраняет адрес сообщения и наличие подписи."""
    return {
        "message_id": message.message_id,
        "chat_id": message.chat.id,
        "has_caption": message.caption is not None
        or message.content_type in {"photo", "video", "animation", "audio", "document", "voice"},
    }


def _checkout_details(temporary: dict | None) -> dict | None:
    """Копирует сохранённую корзину для проверки после ввода."""
    if (
        not temporary
        or temporary.get("state") not in ALLOWED_TEMP_PAYMENT_STATES
        or not isinstance(temporary.get("data"), dict)
    ):
        return None
    return deepcopy({"state": temporary["state"], "data": temporary["data"]})


async def _checkout_current(
    session: AsyncSession, user_ref: TelegramId, amount: Decimal, checkout_snapshot: dict | None
) -> bool:
    """Проверяет неизменность корзины и точной суммы покупки."""
    current = _checkout_details(await get_temporary_data(session, user_ref))
    if current is None or current != checkout_snapshot:
        return False
    try:
        return rub_amount(current["data"].get("required_amount")) == amount
    except (ValueError, ArithmeticError, TypeError):
        return False


async def _request_phone(
    event: Message | CallbackQuery,
    session: AsyncSession,
    amount: Decimal,
    state: FSMContext | None,
    message: Message,
    *,
    back: str,
    fast: bool,
    request_id: str,
    expected_state: str | None,
    checkout_snapshot: dict | None,
) -> None:
    """Сохраняет платёж и запрашивает номер для SberPay."""
    if state is None:
        await edit_or_send_message(
            target_message=message,
            text=texts.PAYHOT_PHONE_STATE_MISSING,
            reply_markup=back_keyboard(back),
        )
        return
    if fast and not await _checkout_current(session, TelegramId(event.from_user.id), amount, checkout_snapshot):
        if await _invoice_context_current(state, request_id, expected_state):
            await edit_or_send_message(
                target_message=message,
                text=texts.PAYHOT_CHECKOUT_CHANGED,
                reply_markup=back_keyboard(back),
            )
        return
    if not await _invoice_context_current(state, request_id, expected_state):
        return
    await state.set_state(ReplenishPayhotState.entering_phone)
    await state.update_data(
        payhot_method="sberpay",
        payhot_prompt=_prompt_details(message),
        payhot_pending={
            "amount": str(amount),
            "back": back,
            "fast": fast,
            "telegram_id": event.from_user.id,
            "checkout": checkout_snapshot,
        },
    )
    if not await _invoice_context_current(state, request_id, ReplenishPayhotState.entering_phone.state):
        return
    try:
        await edit_or_send_message(
            target_message=message,
            text=texts.PAYHOT_ENTER_PHONE,
            reply_markup=back_keyboard(back),
            edit_only=True,
        )
    except Exception:
        if not await _invoice_context_current(state, request_id, ReplenishPayhotState.entering_phone.state):
            return
        prompt = await message.answer(texts.PAYHOT_ENTER_PHONE, reply_markup=back_keyboard(back))
        if await _invoice_context_current(state, request_id, ReplenishPayhotState.entering_phone.state):
            await state.update_data(payhot_prompt=_prompt_details(prompt))


async def _show_invoice(
    event: Message | CallbackQuery,
    session: AsyncSession,
    amount: Decimal,
    method_name: str,
    *,
    state: FSMContext | None = None,
    back: str = "pay",
    fast: bool = False,
    target_message: Message | None = None,
    request_id: str | None = None,
    expected_state: str | None = None,
    customer_phone: str | None = None,
    checkout_snapshot: dict | None = None,
) -> bool:
    """Создаёт счёт и показывает ссылку для оплаты."""
    message = target_message or (event.message if isinstance(event, CallbackQuery) else event)
    if not isinstance(message, Message):
        return False
    if request_id is None:
        expected_state = await state.get_state() if state is not None else None
        request_id = uuid4().hex
        if state is not None:
            await state.update_data(payhot_request_id=request_id)
    if not await _invoice_context_current(state, request_id, expected_state):
        return False
    if not await _available_for(event, method_name, target_message=message, back=back):
        return False
    if method_name == "sberpay" and customer_phone is None:
        await _request_phone(
            event,
            session,
            amount,
            state,
            message,
            back=back,
            fast=fast,
            request_id=request_id,
            expected_state=expected_state,
            checkout_snapshot=checkout_snapshot,
        )
        return False
    try:
        user_ref = TelegramId(event.from_user.id)
        if fast and method_name == "sberpay":
            if not await _checkout_current(session, user_ref, amount, checkout_snapshot):
                raise ValueError(texts.PAYHOT_CHECKOUT_CHANGED)
            if not await _invoice_context_current(state, request_id, expected_state):
                return False
        metadata = (
            {"source": "telegram_fast_flow"}
            if fast
            else {"source": "telegram_balance", "payment_flow": "balance_topup"}
        )
        if method_name == "sberpay":
            metadata["payhot_customer_phone"] = normalize_phone(customer_phone)
        payment_url, _payment_id = await create_invoice(
            session,
            user_ref,
            amount,
            method_name,
            metadata=metadata,
            expected_checkout=checkout_snapshot if fast else None,
        )
        if not await _invoice_context_current(state, request_id, expected_state):
            return False
        amount_text = await format_for_user(
            session,
            user_ref,
            amount,
            event.from_user.language_code,
            force_currency="RUB",
        )
        if amount != amount.to_integral_value():
            amount_text = f"{amount:,.2f} ₽".replace(",", " ")
        if not await _invoice_context_current(state, request_id, expected_state):
            return False
        if state is not None:
            await state.set_state(None)
            await state.update_data(payhot_pending=None)
            expected_state = None
        if not await _invoice_context_current(state, request_id, expected_state):
            return False
        await edit_or_send_message(
            target_message=message,
            text=texts.PAYHOT_INVOICE_CREATED.format(amount=html_escape(amount_text)),
            reply_markup=pay_keyboard(payment_url, pay_text=buttons.PAY_2, back_cb=back),
        )
        return True
    except ValueError as exc:
        error_text = html_escape(str(exc)) or texts.PAYHOT_CREATE_LINK_ERROR
    except Exception as exc:
        logger.error("[PayHOT] Не удалось создать счёт {}: {}", method_name, type(exc).__name__)
        error_text = texts.PAYHOT_CREATE_LINK_ERROR
    if not await _invoice_context_current(state, request_id, expected_state):
        return False
    await edit_or_send_message(target_message=message, text=error_text, reply_markup=back_keyboard(back))
    return False


@router.callback_query(F.data.in_({"pay_payhot_cards", "pay_payhot_sbp", "pay_payhot_sberpay"}))
async def pay_payhot_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Открывает рублёвые суммы выбранного способа PayHOT."""
    method_name = callback.data.removeprefix("pay_payhot_")
    if not await _available_for(callback, method_name):
        return
    await callback.answer()
    await state.set_state(None)
    request_id = uuid4().hex
    await state.update_data(payhot_request_id=request_id, payhot_method=method_name)
    options = await payment_options_for_user(
        session, TelegramId(callback.from_user.id), callback.from_user.language_code, force_currency="RUB"
    )
    if not await _invoice_context_current(state, request_id, None):
        return
    await edit_or_send_message(
        target_message=callback.message,
        text=texts.PAYHOT_PAYMENT_MENU_TITLE.format(method=METHOD_LABELS[method_name]),
        reply_markup=build_amounts_keyboard(
            prefix="payhot",
            pattern=f"{{prefix}}_amount|{method_name}|{{price}}",
            back_cb="pay",
            custom_cb=f"payhot_custom|{method_name}",
            opts=options,
        ),
    )


@router.callback_query(F.data.startswith("payhot_amount|"))
async def pay_payhot_amount(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Создаёт счёт для выбранной рублёвой суммы."""
    try:
        _, method_name, value = callback.data.split("|")
        amount = rub_amount(value)
    except (ValueError, ArithmeticError, TypeError):
        await callback.answer(texts.PAYHOT_INVALID_AMOUNT, show_alert=True)
        return
    if not await _available_for(callback, method_name):
        return
    await callback.answer()
    await state.set_state(None)
    await _show_invoice(callback, session, amount, method_name, state=state)


@router.callback_query(F.data.startswith("payhot_custom|"))
async def custom_amount(callback: CallbackQuery, state: FSMContext) -> None:
    """Сохраняет приглашение к вводу суммы в рублях."""
    method_name = callback.data.removeprefix("payhot_custom|")
    if not await _available_for(callback, method_name):
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.answer()
    await state.set_state(ReplenishPayhotState.entering_custom_amount)
    await state.update_data(
        payhot_method=method_name,
        payhot_request_id=uuid4().hex,
        payhot_prompt=_prompt_details(callback.message),
    )
    await edit_or_send_message(
        target_message=callback.message,
        text=texts.PAYHOT_ENTER_AMOUNT,
        reply_markup=back_keyboard(f"pay_payhot_{method_name}"),
    )


@router.message(ReplenishPayhotState.entering_custom_amount)
async def custom_amount_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Проверяет введённую сумму и заменяет приглашение счётом."""
    expected_state = ReplenishPayhotState.entering_custom_amount.state
    if await state.get_state() != expected_state:
        return
    request_id = uuid4().hex
    await state.update_data(payhot_request_id=request_id)
    data = await state.get_data()
    method_name = data.get("payhot_method", "")
    target_message = _saved_prompt(message, data)
    if not await _available_for(message, method_name, target_message=target_message):
        return
    try:
        amount = rub_amount((message.text or "").strip().replace(",", "."))
    except (ValueError, ArithmeticError, TypeError):
        if not await _invoice_context_current(state, request_id, expected_state):
            return
        await edit_or_send_message(
            target_message=target_message or message,
            text=texts.PAYHOT_INVALID_AMOUNT,
            reply_markup=back_keyboard(f"pay_payhot_{method_name}"),
        )
        return
    if not await _invoice_context_current(state, request_id, expected_state):
        return
    await _show_invoice(
        message,
        session,
        amount,
        method_name,
        state=state,
        target_message=target_message,
        request_id=request_id,
        expected_state=expected_state,
    )


@router.message(ReplenishPayhotState.entering_phone)
async def phone_input(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Проверяет свой номер и создаёт сохранённый счёт SberPay."""
    expected_state = ReplenishPayhotState.entering_phone.state
    if await state.get_state() != expected_state:
        return
    request_id = uuid4().hex
    await state.update_data(payhot_request_id=request_id)
    data = await state.get_data()
    target_message = _saved_prompt(message, data)
    pending = data.get("payhot_pending")
    if not isinstance(pending, dict) or pending.get("telegram_id") != message.from_user.id:
        if await _invoice_context_current(state, request_id, expected_state):
            await edit_or_send_message(
                target_message=target_message or message,
                text=texts.PAYHOT_PHONE_STATE_MISSING,
                reply_markup=back_keyboard("pay"),
            )
        return
    back = pending.get("back")
    if not isinstance(back, str) or not back:
        back = "fastflow_back" if pending.get("fast") is True else "pay"
    if not await _available_for(message, "sberpay", target_message=target_message, back=back):
        return
    try:
        if message.contact is not None:
            if message.contact.user_id != message.from_user.id:
                raise ValueError(texts.PAYHOT_OWN_CONTACT_REQUIRED)
            phone = message.contact.phone_number
        else:
            phone = message.text or ""
        customer_phone = normalize_phone(phone)
        amount = rub_amount(pending.get("amount"))
    except (ValueError, ArithmeticError, TypeError) as exc:
        if not await _invoice_context_current(state, request_id, expected_state):
            return
        await edit_or_send_message(
            target_message=target_message or message,
            text=html_escape(str(exc)) or texts.PAYHOT_ENTER_PHONE,
            reply_markup=back_keyboard(back),
        )
        return
    if not await _invoice_context_current(state, request_id, expected_state):
        return
    await _show_invoice(
        message,
        session,
        amount,
        "sberpay",
        state=state,
        back=back,
        fast=pending.get("fast") is True,
        target_message=target_message,
        request_id=request_id,
        expected_state=expected_state,
        customer_phone=customer_phone,
        checkout_snapshot=pending.get("checkout"),
    )


async def _handle_custom_amount_input_payhot(
    event: CallbackQuery, session: AsyncSession, method_name: str, *, state: FSMContext | None = None
) -> None:
    """Создаёт счёт на точную сумму сохранённой покупки."""
    if not await _available_for(event, method_name, back="fastflow_back"):
        return
    temporary = await get_temporary_data(session, TelegramId(event.from_user.id))
    if (
        not temporary
        or temporary.get("state") not in ALLOWED_TEMP_PAYMENT_STATES
        or not isinstance(temporary.get("data"), dict)
    ):
        await edit_or_send_message(
            target_message=event.message,
            text=texts.PAYHOT_CHECKOUT_MISSING,
            reply_markup=back_keyboard("fastflow_back"),
        )
        return
    try:
        amount = rub_amount(temporary["data"].get("required_amount"))
    except (ValueError, ArithmeticError, TypeError):
        await edit_or_send_message(
            target_message=event.message,
            text=texts.PAYHOT_AMOUNT_UNAVAILABLE,
            reply_markup=back_keyboard("fastflow_back"),
        )
        return
    await _show_invoice(
        event,
        session,
        amount,
        method_name,
        state=state,
        back="fastflow_back",
        fast=True,
        checkout_snapshot=_checkout_details(temporary),
    )


async def handle_custom_amount_input_payhot_cards(event: CallbackQuery, session: AsyncSession) -> None:
    """Создаёт счёт PayHOT для оплаты картой."""
    await _handle_custom_amount_input_payhot(event, session, "cards")


async def handle_custom_amount_input_payhot_sbp(event: CallbackQuery, session: AsyncSession) -> None:
    """Создаёт счёт PayHOT для оплаты через СБП."""
    await _handle_custom_amount_input_payhot(event, session, "sbp")


async def handle_custom_amount_input_payhot_sberpay(
    event: CallbackQuery, session: AsyncSession, *, state: FSMContext | None = None
) -> None:
    """Создаёт счёт PayHOT для оплаты через SberPay."""
    await _handle_custom_amount_input_payhot(event, session, "sberpay", state=state)
