import math

from decimal import Decimal, InvalidOperation
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.payhot_autopay_config import (
    PAYHOT_AUTOPAY_CONFIG,
    is_payhot_autopay_enabled_for_session,
)
from database import payhot_autopay as dal
from database.access.resolution import TelegramId, UserId, get_user_by_tg_id
from database.cache_purge import flush_purges
from database.keys import get_keys
from database.temporary_data import get_temporary_data
from handlers.payments.autopay_management import (
    AutopayManagementProvider,
    management_back_button,
    management_checkout_callback,
    management_checkout_return,
    management_return_callback,
    register_autopay_management,
    remember_management_checkout,
)
from handlers.payments.keyboards import build_amounts_keyboard, payment_options_for_user
from handlers.payments.payhot.handlers import _show_invoice
from handlers.utils import edit_or_send_message
from services.errors import ServiceError
from services.payments.checkout_intent import preview_checkout_autopay_terms
from services.payments.currency_rates import format_for_user
from services.payments.payhot.autopay import (
    cancel_subscription,
    delete_method,
    enable_autopay_for_key,
    quote_autopay_for_key,
)
from services.payments.payhot.service import available
from settings import buttons, texts


router = Router(name="core_payhot_autopay")
_PAGE_SIZE = 6
_FLOW_BY_STATE = {
    "waiting_for_payment": "tariff_purchase",
    "waiting_for_renewal_payment": "key_renewal",
    "waiting_for_addons_payment": "key_addons",
    "waiting_for_gift_payment": "gift_create",
}


class PayHOTPaymentState(StatesGroup):
    entering_amount = State()
    waiting_consent = State()


def _amount(value: Any) -> float | None:
    """Проверяет сумму без округления введённых условий."""
    try:
        amount = Decimal(str(value).replace(",", ".").strip())
        minimum = max(Decimal("0.01"), Decimal(str(PAYHOT_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 0.01))))
        if amount.is_finite() and minimum <= amount <= 1_000_000 and amount == amount.quantize(Decimal("0.01")):
            return float(amount)
    except (InvalidOperation, TypeError, ValueError):
        pass
    return None


async def _actor_uid(event: Message | CallbackQuery, session: AsyncSession) -> UserId | None:
    """Определяет владельца по отправителю Telegram."""
    user = await get_user_by_tg_id(session, TelegramId(event.from_user.id))
    if user is not None:
        return UserId(user.id)
    if isinstance(event, CallbackQuery):
        await _show_checkout_error(event, texts.PAYHOT_AUTOPAY_USER_NOT_FOUND)
    else:
        await event.answer(texts.PAYHOT_AUTOPAY_USER_NOT_FOUND)
    return None


async def _allow_create(
    event: Message | CallbackQuery, session: AsyncSession, *, return_callback: str | None = None
) -> bool:
    """Проверяет доступность новых привязок и оплат."""
    if await is_payhot_autopay_enabled_for_session(session) and any(available(method) for method in ("cards", "sbp")):
        return True
    if isinstance(event, CallbackQuery):
        await _show_checkout_error(event, texts.PAYHOT_AUTOPAY_DISABLED)
    else:
        kwargs = (
            {"reply_markup": _keyboard((buttons.CANCEL, return_callback))}
            if return_callback and return_callback != "pay"
            else {}
        )
        await event.answer(texts.PAYHOT_AUTOPAY_DISABLED, **kwargs)
    return False


async def _commit(session: AsyncSession) -> None:
    """Сохраняет изменения и обновляет кеш."""
    await session.commit()
    await flush_purges(session)


def _keyboard(*rows: tuple[str, str]):
    """Создаёт клавиатуру действий."""
    keyboard = InlineKeyboardBuilder()
    for label, data in rows:
        keyboard.row(InlineKeyboardButton(text=label, callback_data=data))
    return keyboard.as_markup()


async def _show_checkout_error(callback: CallbackQuery, text: str, *, retry: bool = False) -> None:
    """Показывает ошибку оплаты после раннего подтверждения кнопки."""
    rows = []
    if retry:
        rows.append((buttons.PAYHOT_AUTOPAY_CONFIRM, "payhot_auto_confirm"))
    rows.append((buttons.BACK, management_checkout_callback(callback)))
    await edit_or_send_message(callback.message, escape(text), reply_markup=_keyboard(*rows))
    await callback.answer()


async def _show_consent(
    event: Message | CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user_id: UserId,
    amount: float,
    metadata: dict,
    method_name: str,
) -> None:
    """Показывает сумму и условия регулярных списаний."""
    metadata = {**metadata, "autopay_provider": "PAYHOT_AUTOPAY"}
    amount_text = await format_for_user(
        session, user_id, amount, getattr(event.from_user, "language_code", None), force_currency="RUB"
    )
    if Decimal(str(amount)) != Decimal(str(amount)).to_integral_value():
        amount_text = f"{amount:,.2f}".replace(",", " ") + " ₽"
    terms = None
    if metadata.get("payment_flow") in {"tariff_purchase", "key_renewal"}:
        try:
            terms = await preview_checkout_autopay_terms(session, user_id, metadata, provider_id="PAYHOT_AUTOPAY")
        except (ValueError, ServiceError) as exc:
            if isinstance(event, CallbackQuery):
                await _show_checkout_error(event, str(exc))
            else:
                await event.answer(escape(str(exc)))
            return
    if terms:
        metadata.update(
            autopay_accepted_amount=float(terms["amount"]),
            autopay_accepted_period_days=int(terms["period_days"]),
        )
        text = texts.PAYHOT_AUTOPAY_CHECKOUT_CONSENT.format(
            amount=amount_text, recurring_amount=f"{terms['amount']:g} ₽", period_days=terms["period_days"]
        )
    else:
        text = texts.PAYHOT_AUTOPAY_SAVE_METHOD_CONSENT.format(amount=amount_text)
    await state.update_data(
        payhot_checkout={"user_id": int(user_id), "amount": amount, "metadata": metadata, "method": method_name}
    )
    await state.set_state(PayHOTPaymentState.waiting_consent)
    return_callback = await management_checkout_return(state, user_id, "payhot")
    markup = _keyboard((buttons.PAYHOT_AUTOPAY_CONFIRM, "payhot_auto_confirm"), (buttons.CANCEL, return_callback))
    if isinstance(event, CallbackQuery):
        await edit_or_send_message(event.message, text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


async def _show_methods(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user_id: UserId,
    checkout: dict | None = None,
) -> None:
    """Предлагает включённые способы привязки PayHOT."""
    await state.set_state(None)
    await state.update_data(
        payhot_method_choice={"user_id": int(user_id), "checkout": checkout}, payhot_checkout=None, payhot_enable=None
    )
    rows = [
        (texts.PAYHOT_METHOD_CARDS if method == "cards" else texts.PAYHOT_METHOD_SBP, f"payhot_auto_method|{method}")
        for method in ("cards", "sbp")
        if available(method)
    ]
    rows.append((buttons.BACK, await management_checkout_return(state, user_id, "payhot")))
    await edit_or_send_message(callback.message, texts.PAYHOT_AUTOPAY_CHOOSE_METHOD, reply_markup=_keyboard(*rows))
    await callback.answer()


@router.callback_query(F.data == "pay_payhot_autopay", flags={"balance_topup": "checkout"})
async def payment_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Открывает выбор способа оплаты PayHOT с привязкой."""
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    temporary = await get_temporary_data(session, user_id)
    if temporary and temporary["state"] in _FLOW_BY_STATE:
        await process_fast_renewal_flow(callback, session, state=state)
        return
    await remember_management_checkout(callback, state, user_id, "payhot", reset=True)
    await _show_methods(callback, state, session, user_id)


@router.callback_query(F.data.startswith("payhot_auto_method|"), flags={"balance_topup": "payhot_method_choice"})
async def choose_method(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Сохраняет способ и показывает сумму или согласие покупки."""
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    choice = (await state.get_data()).get("payhot_method_choice")
    method_name = callback.data.split("|", 1)[1]
    if not isinstance(choice, dict) or choice.get("user_id") != int(user_id):
        await _show_checkout_error(callback, texts.PAYHOT_AUTOPAY_CONSENT_EXPIRED)
        return
    if method_name not in {"cards", "sbp"} or not available(method_name):
        await _show_checkout_error(callback, texts.PAYHOT_AUTOPAY_UNAVAILABLE)
        return
    await state.update_data(payhot_autopay_method=method_name)
    checkout = choice.get("checkout")
    if isinstance(checkout, dict):
        await _show_consent(callback, state, session, user_id, checkout["amount"], checkout["metadata"], method_name)
        return
    options = await payment_options_for_user(session, user_id, callback.from_user.language_code, force_currency="RUB")
    options = [option for option in options if _amount(option["callback_data"].split("|", 1)[-1]) is not None]
    await edit_or_send_message(
        callback.message,
        texts.PAYHOT_AUTOPAY_PAYMENT_MENU,
        reply_markup=build_amounts_keyboard(
            prefix="payhot_auto",
            pattern="{prefix}_amount|{price}",
            opts=options,
            custom_cb="payhot_auto_custom",
            back_cb=await management_checkout_return(state, user_id, "payhot"),
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payhot_auto_amount|"), flags={"balance_topup": True})
async def quick_amount(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Запрашивает согласие на оплату выбранной суммы."""
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    amount = _amount(callback.data.split("|", 1)[1])
    if amount is None:
        await _show_checkout_error(callback, texts.PAYHOT_AUTOPAY_INVALID_AMOUNT)
        return
    method_name = (await state.get_data()).get("payhot_autopay_method")
    if method_name not in {"cards", "sbp"} or not available(method_name):
        await _show_checkout_error(callback, texts.PAYHOT_AUTOPAY_CONSENT_EXPIRED)
        return
    await _show_consent(callback, state, session, user_id, amount, {"payment_flow": "balance_topup"}, method_name)


@router.callback_query(F.data == "payhot_auto_custom", flags={"balance_topup": True})
async def request_amount(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Запрашивает произвольную сумму."""
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    await state.update_data(payhot_checkout=None)
    await state.set_state(PayHOTPaymentState.entering_amount)
    return_callback = await management_checkout_return(state, user_id, "payhot")
    await edit_or_send_message(
        callback.message, texts.PAYHOT_AUTOPAY_ENTER_AMOUNT, reply_markup=_keyboard((buttons.CANCEL, return_callback))
    )
    await callback.answer()


@router.message(PayHOTPaymentState.entering_amount, flags={"balance_topup": True})
async def custom_amount(message: Message, state: FSMContext, session: AsyncSession):
    """Проверяет введённую сумму и показывает согласие."""
    user_id = await _actor_uid(message, session)
    if user_id is None:
        return
    return_callback = await management_checkout_return(state, user_id, "payhot")
    if not await _allow_create(message, session, return_callback=return_callback):
        return
    amount = _amount(message.text)
    if amount is None:
        kwargs = {"reply_markup": _keyboard((buttons.CANCEL, return_callback))} if return_callback != "pay" else {}
        await message.answer(texts.PAYHOT_AUTOPAY_INVALID_AMOUNT, **kwargs)
        return
    method_name = (await state.get_data()).get("payhot_autopay_method")
    if method_name not in {"cards", "sbp"} or not available(method_name):
        await message.answer(texts.PAYHOT_AUTOPAY_CONSENT_EXPIRED)
        return
    await _show_consent(message, state, session, user_id, amount, {"payment_flow": "balance_topup"}, method_name)


async def process_fast_renewal_flow(
    callback_query: CallbackQuery, session: AsyncSession, state: FSMContext | None = None
):
    """Показывает согласие для сохранённой покупки."""
    user_id = await _actor_uid(callback_query, session)
    if user_id is None or not await _allow_create(callback_query, session):
        return
    if state is not None:
        await remember_management_checkout(callback_query, state, user_id, "payhot")
    temporary = await get_temporary_data(session, user_id)
    if state is None or not temporary or temporary["state"] not in _FLOW_BY_STATE:
        await _show_checkout_error(callback_query, texts.PAYHOT_AUTOPAY_CONSENT_EXPIRED)
        return
    data = dict(temporary.get("data") or {})
    amount = _amount(data.get("required_amount"))
    if amount is None:
        await _show_checkout_error(callback_query, texts.PAYHOT_AUTOPAY_INVALID_AMOUNT)
        return
    flow = _FLOW_BY_STATE[temporary["state"]]
    if flow not in {"tariff_purchase", "key_renewal"} or data.get("payment_flow") == "trial_purchase":
        await _show_checkout_error(callback_query, texts.PAYHOT_AUTOPAY_SUBSCRIPTION_UNAVAILABLE)
        return
    metadata = {"payment_flow": flow, "checkout_intent": {"state": temporary["state"], "data": data}}
    if data.get("client_id"):
        metadata["autopay_client_id"] = str(data["client_id"])
    await _show_methods(callback_query, state, session, user_id, {"amount": amount, "metadata": metadata})


@router.callback_query(F.data == "payhot_auto_confirm", flags={"balance_topup": "payhot_checkout"})
async def confirm_checkout(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Создаёт платёж после согласия подтверждённого владельца."""
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    checkout = (await state.get_data()).get("payhot_checkout")
    if not isinstance(checkout, dict) or checkout.get("user_id") != int(user_id):
        await _show_checkout_error(callback, texts.PAYHOT_AUTOPAY_CONSENT_EXPIRED)
        return
    amount = _amount(checkout.get("amount"))
    if amount is None or not isinstance(checkout.get("metadata"), dict):
        await _show_checkout_error(callback, texts.PAYHOT_AUTOPAY_CONSENT_EXPIRED)
        return
    method_name = checkout.get("method")
    if method_name not in {"cards", "sbp"} or not available(method_name):
        await _show_checkout_error(callback, texts.PAYHOT_AUTOPAY_UNAVAILABLE)
        return
    metadata = {**checkout["metadata"], "autopay_consent": True}
    checkout_snapshot = metadata.get("checkout_intent")
    return_callback = await management_checkout_return(state, user_id, "payhot")
    created = await _show_invoice(
        callback,
        session,
        Decimal(str(amount)),
        method_name,
        state=state,
        fast=isinstance(checkout_snapshot, dict),
        back=return_callback,
        checkout_snapshot=checkout_snapshot,
        metadata=metadata,
    )
    if created:
        await state.clear()
    await callback.answer()


async def _owned_subscription(session: AsyncSession, user_id: UserId, value: str):
    """Находит подписку только у текущего владельца."""
    try:
        subscription_id = int(value)
    except (TypeError, ValueError):
        return None
    return next((item for item in await dal.list_subscriptions(session, user_id) if item.id == subscription_id), None)


async def _render_management(callback: CallbackQuery, session: AsyncSession, user_id: UserId, page: int = 0):
    """Показывает способы оплаты и автопродления."""
    methods = await dal.list_methods(session, user_id)
    subscriptions = [item for item in await dal.list_subscriptions(session, user_id) if item.is_active]
    enabled = await is_payhot_autopay_enabled_for_session(session)
    items = [("method", item) for item in methods] + [("subscription", item) for item in subscriptions]
    if enabled and methods:
        subscribed = {item.client_id for item in subscriptions}
        items += [("key", item) for item in await get_keys(session, user_id) if item.client_id not in subscribed]
    total_pages = max(1, math.ceil(len(items) / _PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    lines = [texts.PAYHOT_AUTOPAY_MANAGEMENT_TITLE]
    if not enabled:
        lines.append(texts.PAYHOT_AUTOPAY_DISABLED)
    if not methods:
        lines.append(texts.PAYHOT_AUTOPAY_NO_METHODS)
    keyboard = InlineKeyboardBuilder()
    for kind, item in items[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]:
        if kind == "method":
            lines.append(
                texts.PAYHOT_AUTOPAY_METHOD_LABEL.format(
                    method=texts.PAYHOT_METHOD_SBP if item.payment_method == "sbp" else texts.PAYHOT_METHOD_CARDS
                )
            )
            keyboard.row(
                InlineKeyboardButton(
                    text=buttons.PAYHOT_AUTOPAY_DELETE_METHOD, callback_data=f"payhot_auto_delete|{item.id}"
                )
            )
        elif kind == "subscription":
            date = item.next_payment_date.strftime("%d.%m.%Y") if item.next_payment_date else "—"
            lines.append(
                texts.PAYHOT_AUTOPAY_SUBSCRIPTION_ITEM.format(
                    client_id=escape(item.client_id), amount=float(item.amount), period_days=item.period_days, date=date
                )
            )
            keyboard.row(
                InlineKeyboardButton(
                    text=buttons.PAYHOT_AUTOPAY_CANCEL_SUBSCRIPTION, callback_data=f"payhot_auto_cancel|{item.id}"
                )
            )
        else:
            label = str(getattr(item, "alias", None) or item.email or item.client_id)[:25]
            keyboard.row(
                InlineKeyboardButton(
                    text=buttons.PAYHOT_AUTOPAY_ENABLE_KEY.format(label=label),
                    callback_data=f"payhot_auto_enable|{item.client_id}",
                )
            )
    navigation = [
        InlineKeyboardButton(text=label, callback_data=f"payhot_auto_page|{target}")
        for target, label in ((page - 1, "⬅️"), (page + 1, "➡️"))
        if 0 <= target < total_pages
    ]
    if navigation:
        keyboard.row(*navigation)
    if enabled:
        keyboard.row(InlineKeyboardButton(text=buttons.PAYHOT_AUTOPAY_ADD_METHOD, callback_data="pay_payhot_autopay"))
    keyboard.row(await management_back_button(callback, session, user_id))
    await edit_or_send_message(callback.message, "\n\n".join(lines), reply_markup=keyboard.as_markup())


@router.callback_query(F.data == "payhot_auto_manage")
@router.callback_query(F.data.startswith("payhot_auto_page|"))
async def manage(callback: CallbackQuery, session: AsyncSession):
    """Открывает управление, включая выключенную кассу."""
    user_id = await _actor_uid(callback, session)
    if user_id is None:
        return
    try:
        page = int(callback.data.split("|", 1)[1]) if "|" in callback.data else 0
    except ValueError:
        page = 0
    await _render_management(callback, session, user_id, page)
    await callback.answer()


@router.callback_query(F.data.startswith("payhot_auto_delete|"))
@router.callback_query(F.data.startswith("payhot_auto_cancel|"))
async def confirm_remove(callback: CallbackQuery, session: AsyncSession):
    """Объясняет последствия отключения или удаления привязки."""
    user_id = await _actor_uid(callback, session)
    if user_id is None:
        return
    action, item_id = callback.data.split("|", 1)
    method = action == "payhot_auto_delete"
    item = (
        await dal.get_owned_method(session, user_id, item_id)
        if method
        else await _owned_subscription(session, user_id, item_id)
    )
    if item is None:
        await callback.answer(texts.PAYHOT_AUTOPAY_ITEM_NOT_FOUND, show_alert=True)
        return
    text = texts.PAYHOT_AUTOPAY_DELETE_CONFIRM if method else texts.PAYHOT_AUTOPAY_CANCEL_CONFIRM
    data = f"payhot_auto_remove_method|{item.id}" if method else f"payhot_auto_remove_sub|{item.id}"
    await edit_or_send_message(
        callback.message,
        text,
        reply_markup=_keyboard(
            (buttons.APPLY, data), (buttons.CANCEL, management_return_callback(callback, "payhot_auto_manage"))
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payhot_auto_remove_method|"))
@router.callback_query(F.data.startswith("payhot_auto_remove_sub|"))
async def remove(callback: CallbackQuery, session: AsyncSession):
    """Отключает принадлежащее пользователю автопродление."""
    user_id = await _actor_uid(callback, session)
    if user_id is None:
        return
    action, item_id = callback.data.split("|", 1)
    method = action == "payhot_auto_remove_method"
    item = (
        await dal.get_owned_method(session, user_id, item_id)
        if method
        else await _owned_subscription(session, user_id, item_id)
    )
    if item is None:
        await callback.answer(texts.PAYHOT_AUTOPAY_ITEM_NOT_FOUND, show_alert=True)
        return
    try:
        removed = (
            await delete_method(session, user_id, item.id)
            if method
            else await cancel_subscription(session, user_id, item.id)
        )
        if not removed:
            await callback.answer(texts.PAYHOT_AUTOPAY_ITEM_NOT_FOUND, show_alert=True)
            return
        await _commit(session)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(texts.PAYHOT_AUTOPAY_REMOVED, show_alert=True)
    await _render_management(callback, session, user_id)


@router.callback_query(F.data.startswith("payhot_auto_enable|"))
async def confirm_enable(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Запрашивает согласие на сумму и срок выбранной подписки."""
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    method = await dal.get_active_method(session, user_id)
    if method is None:
        await callback.answer(texts.PAYHOT_AUTOPAY_NO_METHODS, show_alert=True)
        return
    client_id = callback.data.split("|", 1)[1]
    try:
        quote = await quote_autopay_for_key(session, user_id, client_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.update_data(
        payhot_enable={
            "user_id": int(user_id),
            "client_id": client_id,
            "method_id": method.id,
            "amount": float(quote["amount"]),
            "period_days": int(quote["period_days"]),
        }
    )
    await edit_or_send_message(
        callback.message,
        texts.PAYHOT_AUTOPAY_ENABLE_CONSENT.format(
            label=escape(str(quote.get("label") or client_id)),
            amount=float(quote["amount"]),
            period_days=quote["period_days"],
        ),
        reply_markup=_keyboard(
            (buttons.PAYHOT_AUTOPAY_ENABLE, "payhot_auto_confirm_enable"),
            (buttons.CANCEL, management_return_callback(callback, "payhot_auto_manage")),
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "payhot_auto_confirm_enable")
async def enable(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Включает автопродление по подтверждённым условиям."""
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    consent = (await state.get_data()).get("payhot_enable")
    if not isinstance(consent, dict) or consent.get("user_id") != int(user_id):
        await callback.answer(texts.PAYHOT_AUTOPAY_CONSENT_EXPIRED, show_alert=True)
        return
    try:
        await enable_autopay_for_key(
            session,
            user_id,
            consent["client_id"],
            method_id=consent["method_id"],
            consent={
                "accepted": True,
                "source": "bot",
                "amount": consent["amount"],
                "period_days": consent["period_days"],
            },
        )
        await _commit(session)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await callback.answer(texts.PAYHOT_AUTOPAY_ENABLED, show_alert=True)
    await _render_management(callback, session, user_id)


async def _has_management_data(session: AsyncSession, user_id: UserId) -> bool:
    """Оставляет доступ к сохранённым картам и незавершённым оплатам."""
    if await dal.list_methods(session, user_id):
        return True
    if any(item.is_active for item in await dal.list_subscriptions(session, user_id)):
        return True
    return bool(await dal.list_unfinished_attempts(session, user_id))


register_autopay_management(
    AutopayManagementProvider(
        provider_id="payhot",
        title=buttons.AUTOPAY_PROVIDER_PAYHOT,
        enabled=is_payhot_autopay_enabled_for_session,
        has_data=_has_management_data,
        render=_render_management,
        button_location=lambda: PAYHOT_AUTOPAY_CONFIG.get("MANAGE_BUTTON_LOCATION", "profile"),
        order=40,
    )
)
