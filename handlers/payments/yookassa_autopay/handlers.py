import hashlib
import math
import re

from decimal import Decimal
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.yookassa_autopay_config import (
    YOOKASSA_AUTOPAY_CONFIG,
    is_yookassa_autopay_enabled,
    is_yookassa_autopay_enabled_for_session,
)
from database.access.resolution import (
    TelegramId,
    UserId,
    get_user_by_tg_id,
    parse_user_ref,
    resolve_user_optional,
)
from database.cache_purge import flush_purges
from database.keys import get_keys
from database.tariffs import get_active_tariffs_by_group_code
from database.temporary_data import get_temporary_data
from database.yookassa_autopay import (
    activate_card,
    cancel_subscription,
    delete_card,
    get_active_card,
    get_attempt,
    list_cards,
    list_subscriptions,
    list_unfinished_attempts,
)
from filters.admin import IsAdminFilter
from handlers.admin.users.keyboard import AdminUserEditorCallback
from handlers.utils import edit_or_send_message
from hooks.hooks import register_hook
from logger import logger
from services.payments.checkout_intent import preview_checkout_autopay_terms
from services.payments.currency_rates import format_for_user
from services.payments.yookassa_autopay.service import (
    create_checkout_payment,
    enable_autopay_for_key,
    quote_autopay_for_key,
    resolve_unknown_attempt,
)
from services.payments.yookassa.amounts import precise_yookassa_amount, quote_yookassa_amount
from settings import buttons, texts
from settings.buttons import APPLY, BACK, CANCEL, CUSTOM_AMOUNT, MAIN_MENU, PAY_2


router = Router(name="core_yookassa_autopay")
_PAGE_SIZE = 8
_FLOW_BY_STATE = {
    "waiting_for_payment": "tariff_purchase",
    "waiting_for_renewal_payment": "key_renewal",
    "waiting_for_addons_payment": "key_addons",
    "waiting_for_gift_payment": "gift_create",
}


class YooKassaPaymentState(StatesGroup):
    entering_amount = State()
    waiting_payment = State()
    waiting_consent = State()
    resolving_attempt = State()


async def _actor_uid(
    event: Message | CallbackQuery,
    session: AsyncSession,
    *,
    target_message: Message | None = None,
) -> UserId | None:
    user = await get_user_by_tg_id(session, TelegramId(event.from_user.id))
    if user is None:
        if isinstance(event, CallbackQuery):
            await event.answer(texts.YOOKASSA_AUTOPAY_NOT_FOUND, show_alert=True)
        else:
            await edit_or_send_message(target_message or event, texts.YOOKASSA_AUTOPAY_NOT_FOUND)
        return None
    return UserId(user.id)


async def _allow_create(
    event: Message | CallbackQuery,
    session: AsyncSession,
    *,
    target_message: Message | None = None,
) -> bool:
    if await is_yookassa_autopay_enabled_for_session(session):
        return True
    if isinstance(event, CallbackQuery):
        await event.answer(texts.YOOKASSA_AUTOPAY_DISABLED, show_alert=True)
    else:
        await edit_or_send_message(target_message or event, texts.YOOKASSA_AUTOPAY_DISABLED)
    return False


async def _remember_prompt(state: FSMContext, message: Message) -> None:
    """Сохраняет сообщение бота для следующего ответа."""
    await state.update_data(
        autopay_prompt={
            "message_id": message.message_id,
            "chat_id": message.chat.id,
            "has_caption": message.caption is not None
            or message.content_type
            in {
                "photo",
                "video",
                "animation",
                "audio",
                "document",
                "voice",
            },
        }
    )


async def _restore_prompt(message: Message, state: FSMContext) -> Message:
    """Восстанавливает подсказку бота в текущем чате."""
    prompt = (await state.get_data()).get("autopay_prompt")
    if (
        not isinstance(prompt, dict)
        or prompt.get("chat_id") != message.chat.id
        or type(prompt.get("message_id")) is not int
        or prompt["message_id"] <= 0
    ):
        return message
    return Message(
        message_id=prompt["message_id"],
        date=message.date,
        chat=message.chat,
        message_thread_id=message.message_thread_id,
        business_connection_id=message.business_connection_id,
        caption="" if prompt.get("has_caption") else None,
    ).as_(message.bot)


async def _admin_uid(callback: CallbackQuery, session: AsyncSession) -> UserId | None:
    try:
        ref = parse_user_ref(callback.data.split("|")[1])
        ref = ref if isinstance(ref, UserId) else TelegramId(ref)
        user = await resolve_user_optional(session, ref)
    except (ValueError, IndexError, TypeError):
        user = None
    if user is None:
        await callback.answer(texts.YOOKASSA_AUTOPAY_CLIENT_NOT_FOUND, show_alert=True)
        return None
    return UserId(user.id)


def _amount(value: Any) -> float | None:
    try:
        amount = round(float(str(value).replace(",", ".").strip()), 2)
    except (ValueError, TypeError, OverflowError):
        return None
    minimum = max(0.01, float(YOOKASSA_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)))
    return amount if math.isfinite(amount) and minimum <= amount <= 1_000_000 else None


def _card_token(card_id: str) -> str:
    """Сокращает идентификатор карты для кнопки."""
    return hashlib.sha256(str(card_id).encode("utf-8")).hexdigest()[:24]


def _card_label(card: Any) -> str:
    if card.card_mask == "SBP":
        return (
            texts.YOOKASSA_AUTOPAY_SBP_BANK.format(bank=card.bank_name)
            if card.bank_name
            else texts.YOOKASSA_AUTOPAY_SBP
        )
    return texts.YOOKASSA_AUTOPAY_CARD_LABEL.format(
        card_type=card.card_type or texts.YOOKASSA_AUTOPAY_CARD_TYPE,
        mask=card.card_mask or texts.YOOKASSA_AUTOPAY_EMPTY_VALUE,
    )


async def _owned_card(session: AsyncSession, user_id: UserId, token: str):
    matches = [
        card for card in await list_cards(session, user_id) if str(card.id) == token or _card_token(card.id) == token
    ]
    return matches[0] if len(matches) == 1 else None


async def _owned_subscription(session: AsyncSession, user_id: UserId, subscription_id: str):
    try:
        number = int(subscription_id)
    except (TypeError, ValueError):
        return None
    return next((sub for sub in await list_subscriptions(session, user_id) if sub.id == number), None)


async def _commit(session: AsyncSession) -> None:
    await session.commit()
    await flush_purges(session)


def _consent_keyboard():
    """Возвращает кнопки подтверждения и отмены оплаты."""
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text=buttons.YOOKASSA_AUTOPAY_CONFIRM_PAYMENT, callback_data="yookassa_autopay_confirm")
    )
    keyboard.row(InlineKeyboardButton(text=CANCEL, callback_data="pay"))
    return keyboard.as_markup()


async def _show_consent(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user_id: UserId,
    amount: float,
    metadata: dict[str, Any],
) -> None:
    language_code = getattr(callback.from_user, "language_code", None)
    flow = metadata.get("payment_flow")
    amount_quote = quote_yookassa_amount(amount)
    metadata = {**metadata, "accepted_gross_amount": float(amount_quote.gross)}
    amount_text = await format_for_user(session, user_id, float(amount_quote.gross), language_code, force_currency="RUB")
    amount_text = precise_yookassa_amount(amount_quote.gross, amount_text)
    if flow in {"tariff_purchase", "key_renewal"}:
        try:
            terms = await preview_checkout_autopay_terms(session, user_id, metadata)
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        metadata = {
            **metadata,
            "autopay_accepted_amount": float(terms["amount"]),
            "autopay_accepted_period_days": int(terms["period_days"]),
            "autopay_accepted_gross_amount": float(quote_yookassa_amount(terms["amount"]).gross),
        }
        recurring_amount = await format_for_user(session, user_id, metadata["autopay_accepted_gross_amount"], language_code, force_currency="RUB")
        recurring_amount = precise_yookassa_amount(metadata["autopay_accepted_gross_amount"], recurring_amount)
        text = texts.YOOKASSA_AUTOPAY_CHECKOUT_CONSENT.format(
            amount=amount_text, recurring_amount=recurring_amount, period_days=int(terms["period_days"])
        )
    else:
        template = {
            "balance_topup": texts.YOOKASSA_AUTOPAY_TOPUP_CONSENT,
            "gift_create": texts.YOOKASSA_AUTOPAY_GIFT_CONSENT,
            "key_addons": texts.YOOKASSA_AUTOPAY_ADDONS_CONSENT,
        }.get(flow, texts.YOOKASSA_AUTOPAY_SAVE_CARD_CONSENT)
        text = template.format(amount=amount_text)
    if amount_quote.fee:
        text += "\n" + texts.YOOKASSA_PAYMENT_QUOTE.format(**amount_quote.public("YOOKASSA_AUTOPAY"))
    await state.update_data(autopay_checkout={"user_id": int(user_id), "amount": amount, "metadata": metadata})
    await state.set_state(YooKassaPaymentState.waiting_consent)
    await edit_or_send_message(callback.message, text, reply_markup=_consent_keyboard())
    await callback.answer()


@router.callback_query(F.data == "pay_yookassa_autopay")
async def handle_pay_yookassa_autopay(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    temp = await get_temporary_data(session, user_id)
    if temp and temp["state"] in _FLOW_BY_STATE:
        await process_fast_renewal_flow(callback, session, state=state)
        return
    await show_payment_menu(callback, state, session)


@router.callback_query(F.data == "yookassa_autopay_menu")
async def show_payment_menu(callback: CallbackQuery, state: FSMContext | None, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    if state is not None:
        await state.clear()
    language_code = getattr(callback.from_user, "language_code", None)
    tariffs = await get_active_tariffs_by_group_code(session, "gifts")
    amounts = sorted({int(tariff.price_rub) for tariff in tariffs if tariff.price_rub and tariff.price_rub > 0})
    amounts = amounts or YOOKASSA_AUTOPAY_CONFIG["QUICK_AMOUNTS"]
    kb = InlineKeyboardBuilder()
    for amount in amounts:
        label = await format_for_user(session, user_id, amount, language_code, force_currency="RUB")
        kb.button(text=label, callback_data=f"yookassa_autopay_amount|{amount}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text=CUSTOM_AMOUNT, callback_data="yookassa_autopay_custom_amount"))
    kb.row(InlineKeyboardButton(text=BACK, callback_data="pay"))
    await edit_or_send_message(callback.message, texts.YOOKASSA_AUTOPAY_PAYMENT_MENU_TITLE, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("yookassa_autopay_amount|"))
async def process_quick_amount(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    amount = _amount(callback.data.split("|", 1)[1])
    if amount is None:
        await callback.answer(texts.YOOKASSA_AUTOPAY_INVALID_AMOUNT, show_alert=True)
        return
    await _show_consent(callback, state, session, user_id, amount, {"payment_flow": "balance_topup"})


@router.callback_query(F.data == "yookassa_autopay_custom_amount")
async def request_custom_amount(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not await _allow_create(callback, session):
        return
    await state.set_state(YooKassaPaymentState.entering_amount)
    await _remember_prompt(state, callback.message)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=CANCEL, callback_data="yookassa_autopay_menu"))
    text = texts.YOOKASSA_AUTOPAY_ENTER_AMOUNT_TEXT.format(
        min_amount=YOOKASSA_AUTOPAY_CONFIG.get("MIN_PAYMENT_AMOUNT", 1)
    )
    await edit_or_send_message(callback.message, text, reply_markup=kb.as_markup())
    await callback.answer()


@router.message(YooKassaPaymentState.entering_amount)
async def process_custom_amount(message: Message, state: FSMContext, session: AsyncSession):
    target_message = await _restore_prompt(message, state)
    user_id = await _actor_uid(message, session, target_message=target_message)
    if user_id is None or not await _allow_create(message, session, target_message=target_message):
        return
    amount = _amount(message.text)
    if amount is None:
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text=CANCEL, callback_data="yookassa_autopay_menu"))
        await edit_or_send_message(target_message, texts.YOOKASSA_AUTOPAY_INVALID_AMOUNT, reply_markup=kb.as_markup())
        return
    amount_quote = quote_yookassa_amount(amount)
    await state.update_data(
        autopay_checkout={"user_id": int(user_id), "amount": amount,
                          "metadata": {"payment_flow": "balance_topup", "accepted_gross_amount": float(amount_quote.gross)}}
    )
    await state.set_state(YooKassaPaymentState.waiting_consent)
    language_code = getattr(message.from_user, "language_code", None)
    amount_text = await format_for_user(session, user_id, float(amount_quote.gross), language_code, force_currency="RUB")
    amount_text = precise_yookassa_amount(amount_quote.gross, amount_text)
    await edit_or_send_message(
        target_message,
        texts.YOOKASSA_AUTOPAY_TOPUP_CONSENT.format(amount=amount_text) + (
            "\n" + texts.YOOKASSA_PAYMENT_QUOTE.format(**amount_quote.public("YOOKASSA_AUTOPAY")) if amount_quote.fee else ""
        ),
        reply_markup=_consent_keyboard(),
    )


async def process_fast_renewal_flow(
    callback_query: CallbackQuery, session: AsyncSession, state: FSMContext | None = None
):
    user_id = await _actor_uid(callback_query, session)
    if user_id is None or not await _allow_create(callback_query, session):
        return
    if state is None:
        await callback_query.answer(texts.YOOKASSA_AUTOPAY_CONSENT_EXPIRED, show_alert=True)
        return
    temp = await get_temporary_data(session, user_id)
    if not temp or temp["state"] not in _FLOW_BY_STATE:
        await show_payment_menu(callback_query, state, session)
        return
    data = dict(temp.get("data") or {})
    amount = _amount(data.get("required_amount"))
    if amount is None:
        await callback_query.answer(texts.YOOKASSA_AUTOPAY_CONSENT_EXPIRED, show_alert=True)
        return
    flow = _FLOW_BY_STATE[temp["state"]]
    if temp["state"] == "waiting_for_payment" and data.get("payment_flow") == "trial_purchase":
        flow = "trial_purchase"
    metadata: dict[str, Any] = {"payment_flow": flow, "checkout_intent": {"state": temp["state"], "data": data}}
    if flow in {"key_renewal", "key_addons"} and data.get("client_id"):
        metadata["autopay_client_id"] = str(data["client_id"])
    await _show_consent(callback_query, state, session, user_id, amount, metadata)


@router.callback_query(F.data == "yookassa_autopay_confirm")
async def confirm_checkout(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    checkout = (await state.get_data()).get("autopay_checkout")
    if not isinstance(checkout, dict) or checkout.get("user_id") != int(user_id):
        await callback.answer(texts.YOOKASSA_AUTOPAY_CONSENT_EXPIRED, show_alert=True)
        return
    amount = _amount(checkout.get("amount"))
    if amount is None or not isinstance(checkout.get("metadata"), dict):
        await callback.answer(texts.YOOKASSA_AUTOPAY_CONSENT_EXPIRED, show_alert=True)
        return
    metadata = dict(checkout["metadata"])
    metadata["autopay_consent"] = True
    await callback.answer()
    try:
        url, _payment_id = await create_checkout_payment(session, user_id, amount, metadata=metadata)
        await _commit(session)
    except ValueError as exc:
        await edit_or_send_message(callback.message, escape(str(exc)), reply_markup=_consent_keyboard())
        return
    except Exception as exc:
        logger.exception("[Autopay] Не удалось создать платёж клиента {}: {}", user_id, exc)
        await edit_or_send_message(
            callback.message, texts.YOOKASSA_AUTOPAY_PAYMENT_ERROR, reply_markup=_consent_keyboard()
        )
        return
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=PAY_2, url=url))
    kb.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    language_code = getattr(callback.from_user, "language_code", None)
    amount_text = await format_for_user(session, user_id, float(metadata.get("accepted_gross_amount") or amount), language_code, force_currency="RUB")
    amount_text = precise_yookassa_amount(metadata.get("accepted_gross_amount") or amount, amount_text)
    await edit_or_send_message(
        callback.message, texts.YOOKASSA_AUTOPAY_PAYMENT_CREATED.format(amount=amount_text), reply_markup=kb.as_markup()
    )


@router.callback_query(F.data.startswith("yookassa_cancel_pending|"))
async def cancel_pending_payment(callback: CallbackQuery, session: AsyncSession):
    if await _actor_uid(callback, session) is None:
        return
    await callback.answer(texts.YOOKASSA_AUTOPAY_CANCEL_PENDING, show_alert=True)


async def _render_management(
    callback: CallbackQuery, session: AsyncSession, user_id: UserId, *, admin: bool = False, page: int = 0
):
    cards = await list_cards(session, user_id)
    subscriptions = [sub for sub in await list_subscriptions(session, user_id) if sub.is_active]
    enabled = await is_yookassa_autopay_enabled_for_session(session)
    items = [("card", card) for card in cards] + [("sub", sub) for sub in subscriptions]
    if not admin and enabled and cards:
        subscribed = {sub.client_id for sub in subscriptions}
        items += [("key", key) for key in await get_keys(session, user_id) if key.client_id not in subscribed]
    if admin:
        items += [("attempt", attempt) for attempt in await list_unfinished_attempts(session, user_id)]
    total_pages = max(1, math.ceil(len(items) / _PAGE_SIZE))
    page = min(max(0, page), total_pages - 1)
    lines = [texts.YOOKASSA_AUTOPAY_MANAGEMENT_TITLE]
    if admin:
        lines.append(texts.YOOKASSA_AUTOPAY_MANAGEMENT_OWNER.format(user_id=int(user_id)))
    if not enabled:
        lines.append(texts.YOOKASSA_AUTOPAY_DISABLED)
    if not cards:
        lines.append(texts.YOOKASSA_AUTOPAY_NO_SAVED_CARDS)
    kb = InlineKeyboardBuilder()
    owner = f"u{int(user_id)}"
    for kind, item in items[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]:
        if kind == "card":
            label = _card_label(item)
            lines.append(
                (
                    texts.YOOKASSA_AUTOPAY_CARD_ACTIVE_ITEM
                    if item.is_active
                    else texts.YOOKASSA_AUTOPAY_CARD_INACTIVE_ITEM
                ).format(label=escape(label))
            )
            token = _card_token(item.id)
            if not admin and not item.is_active:
                kb.row(
                    InlineKeyboardButton(
                        text=buttons.YOOKASSA_AUTOPAY_USE_CARD, callback_data=f"yookassa_activate_card|{token}"
                    )
                )
            delete_callback = f"yk_adm_del|{owner}|{token}" if admin else f"yookassa_delete_card|{token}"
            kb.row(
                InlineKeyboardButton(
                    text=buttons.YOOKASSA_AUTOPAY_DELETE_CARD.format(label=label), callback_data=delete_callback
                )
            )
        elif kind == "sub":
            accepted_amount = getattr(item, "accepted_gross_amount", None)
            date = (
                item.next_payment_date.strftime("%d.%m.%Y")
                if item.next_payment_date
                else texts.YOOKASSA_AUTOPAY_EMPTY_VALUE
            )
            lines.append(
                texts.YOOKASSA_AUTOPAY_SUBSCRIPTION_ITEM.format(
                    client_id=escape(item.client_id), amount=float(accepted_amount if accepted_amount is not None else item.amount), date=date
                )
            )
            cancel_callback = f"yk_adm_unsub|{owner}|{item.id}" if admin else f"yookassa_cancel_subscription|{item.id}"
            kb.row(
                InlineKeyboardButton(
                    text=buttons.YOOKASSA_AUTOPAY_CANCEL_SUBSCRIPTION_NUMBER.format(subscription_id=item.id),
                    callback_data=cancel_callback,
                )
            )
        elif kind == "attempt":
            lines.append(
                texts.YOOKASSA_AUTOPAY_ATTEMPT_ITEM.format(attempt_id=escape(item.id), status=escape(item.status))
            )
            if item.payment_id:
                lines.append(texts.YOOKASSA_AUTOPAY_PAYMENT_ID.format(payment_id=escape(item.payment_id)))
            if item.status in {"unknown", "manual_review", "pending"}:
                kb.row(
                    InlineKeyboardButton(
                        text=buttons.YOOKASSA_AUTOPAY_CHECK_PAYMENT_ID, callback_data=f"yk_adm_resolve|{item.id}"
                    )
                )
        else:
            label = str(getattr(item, "alias", None) or item.email or item.client_id)
            kb.row(
                InlineKeyboardButton(
                    text=buttons.YOOKASSA_AUTOPAY_ENABLE_KEY.format(label=label[:30]),
                    callback_data=f"yookassa_enable_key|{item.client_id}",
                )
            )
    if total_pages > 1:
        row = []
        for target, text in (
            (page - 1, buttons.YOOKASSA_AUTOPAY_PREVIOUS_PAGE),
            (page + 1, buttons.YOOKASSA_AUTOPAY_NEXT_PAGE),
        ):
            if 0 <= target < total_pages:
                data = f"yk_adm_menu|{owner}|{target}" if admin else f"yookassa_manage_page|{target}"
                row.append(InlineKeyboardButton(text=text, callback_data=data))
        kb.row(*row)
        lines.append(texts.YOOKASSA_AUTOPAY_PAGE_ITEM.format(page=page + 1, total_pages=total_pages))
    if not admin and enabled:
        kb.row(
            InlineKeyboardButton(
                text=buttons.YOOKASSA_AUTOPAY_ADD_PAYMENT_METHOD, callback_data="yookassa_autopay_menu"
            )
        )
    if admin:
        kb.row(
            InlineKeyboardButton(
                text=BACK,
                callback_data=AdminUserEditorCallback(action="users_editor", user_id=user_id, edit=True).pack(),
            )
        )
    else:
        kb.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    await edit_or_send_message(callback.message, "\n\n".join(lines), reply_markup=kb.as_markup())


@router.callback_query(F.data == "yookassa_manage_subscriptions")
@router.callback_query(F.data.startswith("yookassa_manage_page|"))
async def show_subscriptions_menu(callback: CallbackQuery, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None:
        return
    try:
        page = int(callback.data.split("|")[1]) if "|" in callback.data else 0
    except (ValueError, IndexError):
        page = 0
    await _render_management(callback, session, user_id, page=page)
    await callback.answer()


async def _confirm_delete(
    callback: CallbackQuery, session: AsyncSession, user_id: UserId, token: str, *, admin: bool = False
):
    card = await _owned_card(session, user_id, token)
    if card is None:
        await callback.answer(texts.YOOKASSA_AUTOPAY_CARD_NOT_FOUND, show_alert=True)
        return
    count = sum(sub.is_active and sub.card_id == card.id for sub in await list_subscriptions(session, user_id))
    text = texts.YOOKASSA_AUTOPAY_DELETE_CARD_CONFIRM.format(card=escape(_card_label(card)))
    if count:
        text += texts.YOOKASSA_AUTOPAY_DELETE_CARD_CONSEQUENCE.format(count=count)
    kb = InlineKeyboardBuilder()
    token = _card_token(card.id)
    confirm = f"yk_adm_cdel|u{int(user_id)}|{token}" if admin else f"yookassa_confirm_delete|{token}"
    back = f"yk_adm_menu|u{int(user_id)}" if admin else "yookassa_manage_subscriptions"
    kb.row(InlineKeyboardButton(text=APPLY, callback_data=confirm))
    kb.row(InlineKeyboardButton(text=CANCEL, callback_data=back))
    await edit_or_send_message(callback.message, text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("yookassa_delete_card|"))
async def confirm_card_deletion(callback: CallbackQuery, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is not None:
        await _confirm_delete(callback, session, user_id, callback.data.split("|", 1)[1])


@router.callback_query(F.data.startswith("yookassa_confirm_delete|"))
async def delete_card_handler(callback: CallbackQuery, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None:
        return
    card = await _owned_card(session, user_id, callback.data.split("|", 1)[1])
    deleted = card is not None and await delete_card(session, user_id, card.id)
    if deleted:
        await _commit(session)
    await callback.answer(
        texts.YOOKASSA_AUTOPAY_CARD_DELETED if deleted else texts.YOOKASSA_AUTOPAY_CARD_NOT_FOUND, show_alert=True
    )
    await _render_management(callback, session, user_id)


@router.callback_query(F.data.startswith("yookassa_activate_card|"))
async def activate_card_handler(callback: CallbackQuery, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None:
        return
    card = await _owned_card(session, user_id, callback.data.split("|", 1)[1])
    activated = card is not None and await activate_card(session, user_id, card.id)
    if activated:
        await _commit(session)
    await callback.answer(
        texts.YOOKASSA_AUTOPAY_CARD_ACTIVATED if activated else texts.YOOKASSA_AUTOPAY_CARD_NOT_FOUND, show_alert=True
    )
    await _render_management(callback, session, user_id)


async def _confirm_cancel(
    callback: CallbackQuery, session: AsyncSession, user_id: UserId, sub_id: str, *, admin: bool = False
):
    sub = await _owned_subscription(session, user_id, sub_id)
    if sub is None or not sub.is_active:
        await callback.answer(texts.YOOKASSA_AUTOPAY_SUBSCRIPTION_NOT_FOUND, show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    confirm = f"yk_adm_cusub|u{int(user_id)}|{sub.id}" if admin else f"yookassa_confirm_cancel|{sub.id}"
    back = f"yk_adm_menu|u{int(user_id)}" if admin else "yookassa_manage_subscriptions"
    kb.row(InlineKeyboardButton(text=buttons.YOOKASSA_AUTOPAY_CANCEL_SUBSCRIPTION, callback_data=confirm))
    kb.row(InlineKeyboardButton(text=CANCEL, callback_data=back))
    await edit_or_send_message(
        callback.message,
        texts.YOOKASSA_AUTOPAY_CANCEL_CONFIRM.format(client_id=escape(sub.client_id)),
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("yookassa_cancel_subscription|"))
async def confirm_cancel_subscription(callback: CallbackQuery, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is not None:
        await _confirm_cancel(callback, session, user_id, callback.data.split("|", 1)[1])


@router.callback_query(F.data.startswith("yookassa_confirm_cancel|"))
async def cancel_subscription_handler(callback: CallbackQuery, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None:
        return
    sub = await _owned_subscription(session, user_id, callback.data.split("|", 1)[1])
    canceled = sub is not None and await cancel_subscription(session, user_id, sub.id)
    if canceled:
        await _commit(session)
    await callback.answer(
        texts.YOOKASSA_AUTOPAY_SUBSCRIPTION_CANCELED if canceled else texts.YOOKASSA_AUTOPAY_SUBSCRIPTION_NOT_FOUND,
        show_alert=True,
    )
    await _render_management(callback, session, user_id)


@router.callback_query(F.data.startswith("yookassa_enable_key|"))
async def confirm_enable_key(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    card = await get_active_card(session, user_id)
    if card is None:
        card = next(iter(await list_cards(session, user_id)), None)
    if card is None:
        await callback.answer(texts.YOOKASSA_AUTOPAY_CARD_REQUIRED, show_alert=True)
        return
    client_id = callback.data.split("|", 1)[1]
    try:
        quote = await quote_autopay_for_key(session, user_id, client_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.update_data(
        autopay_enable={
            "user_id": int(user_id),
            "client_id": client_id,
            "card_id": str(card.id),
            "amount": float(quote["amount"]),
            "gross_amount": float(quote["gross_amount"]),
            "period_days": int(quote["period_days"]),
        }
    )
    language_code = getattr(callback.from_user, "language_code", None)
    amount_text = await format_for_user(session, user_id, quote["gross_amount"], language_code, force_currency="RUB")
    amount_text = precise_yookassa_amount(quote["gross_amount"], amount_text)
    text = texts.YOOKASSA_AUTOPAY_ENABLE_CONSENT.format(
        label=escape(str(quote.get("label") or client_id)),
        amount=amount_text,
        period_days=quote["period_days"],
        card=escape(_card_label(card)),
    )
    amount_quote = quote.get("amount_quote") or {}
    if Decimal(str(amount_quote.get("fee_amount") or 0)) > 0:
        text += texts.YOOKASSA_PAYMENT_QUOTE.format(**amount_quote)
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=buttons.YOOKASSA_AUTOPAY_ENABLE_SUBSCRIPTION, callback_data=f"yookassa_confirm_enable|{client_id}"
        )
    )
    kb.row(InlineKeyboardButton(text=CANCEL, callback_data="yookassa_manage_subscriptions"))
    await edit_or_send_message(callback.message, text, reply_markup=kb.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("yookassa_confirm_enable|"))
async def enable_key_handler(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id = await _actor_uid(callback, session)
    if user_id is None or not await _allow_create(callback, session):
        return
    client_id = callback.data.split("|", 1)[1]
    consent = (await state.get_data()).get("autopay_enable")
    if not isinstance(consent, dict) or consent.get("user_id") != int(user_id) or consent.get("client_id") != client_id:
        await callback.answer(texts.YOOKASSA_AUTOPAY_CONSENT_EXPIRED, show_alert=True)
        return
    card = await _owned_card(session, user_id, str(consent.get("card_id")))
    if card is None:
        await callback.answer(texts.YOOKASSA_AUTOPAY_CONSENT_EXPIRED, show_alert=True)
        return
    try:
        quote = await quote_autopay_for_key(session, user_id, client_id)
        if (float(quote["amount"]) != consent.get("amount") or int(quote["period_days"]) != consent.get("period_days")
                or float(quote["gross_amount"]) != consent.get("gross_amount")):
            await callback.answer(texts.YOOKASSA_AUTOPAY_TERMS_CHANGED, show_alert=True)
            return
        await enable_autopay_for_key(
            session,
            user_id,
            client_id,
            str(card.id),
            consent={
                "accepted": True,
                "source": "bot",
                "amount": consent["amount"],
                "gross_amount": consent["gross_amount"],
                "period_days": consent["period_days"],
            },
        )
        await _commit(session)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await state.clear()
    await callback.answer(texts.YOOKASSA_AUTOPAY_SUBSCRIPTION_ENABLED, show_alert=True)
    await _render_management(callback, session, user_id)


@router.callback_query(F.data.startswith("yk_adm_menu|"), IsAdminFilter())
async def admin_autopay_menu(callback: CallbackQuery, session: AsyncSession, **kwargs: Any):
    user_id = await _admin_uid(callback, session)
    if user_id is None:
        return
    try:
        page = int(callback.data.split("|")[2]) if callback.data.count("|") > 1 else 0
    except (ValueError, IndexError):
        page = 0
    await _render_management(callback, session, user_id, admin=True, page=page)
    await callback.answer()


@router.callback_query(F.data.startswith("yk_adm_del|"), IsAdminFilter())
async def admin_confirm_delete_card(callback: CallbackQuery, session: AsyncSession, **kwargs: Any):
    user_id = await _admin_uid(callback, session)
    parts = callback.data.split("|")
    if user_id is not None and len(parts) == 3:
        await _confirm_delete(callback, session, user_id, parts[2], admin=True)


@router.callback_query(F.data.startswith("yk_adm_cdel|"), IsAdminFilter())
async def admin_execute_delete_card(callback: CallbackQuery, session: AsyncSession, **kwargs: Any):
    user_id = await _admin_uid(callback, session)
    parts = callback.data.split("|")
    if user_id is None or len(parts) != 3:
        return
    card = await _owned_card(session, user_id, parts[2])
    deleted = card is not None and await delete_card(session, user_id, card.id)
    if deleted:
        await _commit(session)
    await callback.answer(
        texts.YOOKASSA_AUTOPAY_CARD_DELETED if deleted else texts.YOOKASSA_AUTOPAY_CARD_NOT_FOUND, show_alert=True
    )
    await _render_management(callback, session, user_id, admin=True)


@router.callback_query(F.data.startswith("yk_adm_unsub|"), IsAdminFilter())
async def admin_confirm_cancel_sub(callback: CallbackQuery, session: AsyncSession, **kwargs: Any):
    user_id = await _admin_uid(callback, session)
    parts = callback.data.split("|")
    if user_id is not None and len(parts) == 3:
        await _confirm_cancel(callback, session, user_id, parts[2], admin=True)


@router.callback_query(F.data.startswith("yk_adm_cusub|"), IsAdminFilter())
async def admin_execute_cancel_sub(callback: CallbackQuery, session: AsyncSession, **kwargs: Any):
    user_id = await _admin_uid(callback, session)
    parts = callback.data.split("|")
    if user_id is None or len(parts) != 3:
        return
    sub = await _owned_subscription(session, user_id, parts[2])
    canceled = sub is not None and await cancel_subscription(session, user_id, sub.id)
    if canceled:
        await _commit(session)
    await callback.answer(
        texts.YOOKASSA_AUTOPAY_SUBSCRIPTION_CANCELED if canceled else texts.YOOKASSA_AUTOPAY_SUBSCRIPTION_NOT_FOUND,
        show_alert=True,
    )
    await _render_management(callback, session, user_id, admin=True)


@router.callback_query(F.data.startswith("yk_adm_resolve|"), IsAdminFilter())
async def admin_request_attempt_resolution(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    attempt_id = callback.data.split("|", 1)[1]
    attempt = await get_attempt(session, attempt_id)
    if attempt is None or attempt.user_id is None or attempt.status not in {"unknown", "manual_review", "pending"}:
        await callback.answer(texts.YOOKASSA_AUTOPAY_ATTEMPT_NOT_FOUND, show_alert=True)
        return
    await state.update_data(autopay_resolution={"attempt_id": attempt.id, "user_id": int(attempt.user_id)})
    await state.set_state(YooKassaPaymentState.resolving_attempt)
    await _remember_prompt(state, callback.message)
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=CANCEL, callback_data=f"yk_adm_menu|u{int(attempt.user_id)}"))
    await edit_or_send_message(
        callback.message,
        texts.YOOKASSA_AUTOPAY_RESOLVE_PROMPT.format(attempt_id=escape(attempt.id)),
        reply_markup=kb.as_markup(),
    )
    await callback.answer()


@router.message(YooKassaPaymentState.resolving_attempt, IsAdminFilter())
async def admin_resolve_attempt_input(message: Message, state: FSMContext, session: AsyncSession):
    target_message = await _restore_prompt(message, state)
    resolution = (await state.get_data()).get("autopay_resolution")
    resolution_user_id = resolution.get("user_id") if isinstance(resolution, dict) else None
    back = (
        f"yk_adm_menu|u{resolution_user_id}"
        if type(resolution_user_id) is int and resolution_user_id > 0
        else "profile"
    )
    cancel_keyboard = InlineKeyboardBuilder()
    cancel_keyboard.row(InlineKeyboardButton(text=CANCEL, callback_data=back))
    cancel_markup = cancel_keyboard.as_markup()
    payment_id = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", payment_id):
        await edit_or_send_message(target_message, texts.YOOKASSA_AUTOPAY_RESOLVE_ID_ONLY, reply_markup=cancel_markup)
        return
    if not isinstance(resolution, dict):
        await edit_or_send_message(target_message, texts.YOOKASSA_AUTOPAY_RESOLVE_REOPEN, reply_markup=cancel_markup)
        return
    attempt = await get_attempt(session, str(resolution.get("attempt_id") or ""))
    if attempt is None or attempt.user_id is None or attempt.user_id != resolution.get("user_id"):
        await edit_or_send_message(target_message, texts.YOOKASSA_AUTOPAY_ATTEMPT_NOT_FOUND, reply_markup=cancel_markup)
        return
    await _commit(session)
    try:
        result = await resolve_unknown_attempt(attempt.id, payment_id)
    except Exception as exc:
        logger.exception("[Autopay] Не удалось сверить попытку {}: {}", attempt.id, exc)
        await edit_or_send_message(target_message, texts.YOOKASSA_AUTOPAY_RESOLVE_ERROR, reply_markup=cancel_markup)
        return
    if not result.ok:
        await edit_or_send_message(
            target_message,
            escape(str(result.error or texts.YOOKASSA_AUTOPAY_RESOLVE_FAILED)),
            reply_markup=cancel_markup,
        )
        return
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=buttons.YOOKASSA_AUTOPAY_CLIENT_MANAGEMENT, callback_data=f"yk_adm_menu|u{int(attempt.user_id)}"
        )
    )
    await edit_or_send_message(
        target_message, escape(result.error or texts.YOOKASSA_AUTOPAY_RESOLVE_COMPLETED), reply_markup=kb.as_markup()
    )


async def _manage_button(chat_id: int, session: AsyncSession):
    user = await get_user_by_tg_id(session, TelegramId(chat_id))
    if user is None:
        return None
    user_id = UserId(user.id)
    if (
        not is_yookassa_autopay_enabled()
        and not await list_cards(session, user_id)
        and not await list_subscriptions(session, user_id)
    ):
        return None
    return InlineKeyboardButton(
        text=buttons.YOOKASSA_AUTOPAY_MANAGE_SUBSCRIPTION, callback_data="yookassa_manage_subscriptions"
    )


async def profile_menu_hook(chat_id: int, session: AsyncSession, **kwargs: Any):
    if YOOKASSA_AUTOPAY_CONFIG.get("MANAGE_BUTTON_LOCATION", "profile") not in {"profile", "both"}:
        return []
    button = await _manage_button(chat_id, session)
    return [{"after": "gifts", "button": button}] if button is not None else []


async def pay_menu_buttons_hook(chat_id: int, session: AsyncSession, **kwargs: Any):
    if YOOKASSA_AUTOPAY_CONFIG.get("MANAGE_BUTTON_LOCATION", "profile") not in {"pay_menu", "both"}:
        return []
    button = await _manage_button(chat_id, session)
    return [{"button": button}] if button is not None else []


async def admin_user_edit_hook(user_id: int | None = None, **kwargs: Any):
    if user_id is None:
        return None
    return {
        "button": InlineKeyboardButton(
            text=buttons.YOOKASSA_AUTOPAY_MANAGE_SUBSCRIPTION, callback_data=f"yk_adm_menu|u{int(user_id)}"
        )
    }


register_hook("profile_menu", profile_menu_hook)
register_hook("pay_menu_buttons", pay_menu_buttons_hook)
register_hook("admin_user_edit", admin_user_edit_hook)
