import os
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import DONATIONS_ENABLE, TRIBUTE_LINK
from core.bootstrap import PAYMENTS_CONFIG, BUTTONS_CONFIG
from core.settings.money_config import get_currency_mode
from database import get_last_payments
from database.models import User
from handlers import buttons as btn
from handlers.payments.currency_flow import build_currency_choice_kb
from handlers.payments.currency_rates import format_for_user
from handlers.payments.providers import get_providers_with_hooks
from handlers.payments.stars.handlers import process_callback_pay_stars
from handlers.payments.tribute.handlers import process_callback_pay_tribute
from handlers.texts import (
    FAST_PAY_CHOOSE_CURRENCY,
    BALANCE_MANAGEMENT_TEXT,
    PAYMENT_METHODS_MSG,
    BALANCE_HISTORY_HEADER,
)
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks

from ..utils import edit_or_send_message


router = Router()


async def get_payment_providers_config() -> dict[str, bool]:
    config = PAYMENTS_CONFIG or {}
    return dict(config)


@router.callback_query(F.data == "pay")
async def handle_pay(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    providers_config = await get_payment_providers_config()
    providers_with_hooks = await get_providers_with_hooks(providers_config)

    mode, one_screen = get_currency_mode()
    multicurrency_enabled = mode == "RUB+USD"

    tribute_cfg = providers_with_hooks.get("TRIBUTE") or {}
    tribute_link = (TRIBUTE_LINK or "").strip()
    tribute_enabled = bool(tribute_cfg.get("enabled") and tribute_link)

    if not multicurrency_enabled:
        allowed_currency = "RUB" if mode == "RUB" else "USD"
        filtered: dict[str, dict[str, Any]] = {}
        for key, cfg in providers_with_hooks.items():
            currency = str(cfg.get("currency") or "").upper()
            if currency in (allowed_currency, "RUB+USD"):
                filtered[key] = cfg
        providers_with_hooks = filtered

    payment_handlers = []

    for key, cfg in providers_with_hooks.items():
        if not cfg.get("enabled"):
            continue
        handler_callback_data = cfg.get("value")
        if not handler_callback_data:
            continue
        handler = globals().get(f"process_callback_{handler_callback_data}")
        if callable(handler):
            payment_handlers.append(handler)

    module_buttons = await run_hooks(
        "pay_menu_buttons",
        chat_id=callback_query.from_user.id,
        admin=False,
        session=session,
    )

    donations_enabled = bool(BUTTONS_CONFIG.get("DONATIONS_BUTTON_ENABLE", DONATIONS_ENABLE))
    has_extra_menu_items = bool(module_buttons) or donations_enabled or tribute_enabled

    if multicurrency_enabled and not one_screen:
        show_stars = bool((providers_with_hooks.get("STARS") or {}).get("enabled"))
        keyboard = build_currency_choice_kb(
            show_stars=show_stars,
            prefix="pay_currency",
            show_tribute=tribute_enabled,
        )
        await edit_or_send_message(
            target_message=callback_query.message,
            text=FAST_PAY_CHOOSE_CURRENCY,
            reply_markup=keyboard.as_markup(),
        )
        return

    if not has_extra_menu_items:
        enabled_providers_count = sum(1 for _, cfg in providers_with_hooks.items() if cfg.get("enabled"))
        if enabled_providers_count == 1 and len(payment_handlers) == 1:
            return await payment_handlers[0](callback_query, state, session)

    builder = InlineKeyboardBuilder()
    for key, cfg in providers_with_hooks.items():
        if not cfg.get("enabled"):
            continue
        if key == "TRIBUTE":
            continue
        text = getattr(btn, key, key)
        builder.row(InlineKeyboardButton(text=text, callback_data=cfg["value"]))

    if tribute_enabled:
        builder.row(
            InlineKeyboardButton(
                text=getattr(btn, "TRIBUTE", "TRIBUTE"),
                url=tribute_link,
            )
        )

    if donations_enabled:
        builder.row(InlineKeyboardButton(text=btn.DONAT_BUTTON, callback_data="donate"))

    builder = insert_hook_buttons(builder, module_buttons)
    builder.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=PAYMENT_METHODS_MSG,
        reply_markup=builder.as_markup(),
    )


async def _build_pay_menu_for_currency(currency: str) -> InlineKeyboardBuilder:
    providers_config = await get_payment_providers_config()
    providers_with_hooks = await get_providers_with_hooks(providers_config)

    builder = InlineKeyboardBuilder()
    for key, cfg in providers_with_hooks.items():
        if key == "TRIBUTE":
            continue
        if not cfg.get("enabled"):
            continue
        if cfg.get("currency") != currency:
            continue
        text = getattr(btn, key, key)
        builder.row(InlineKeyboardButton(text=text, callback_data=cfg["value"]))
    return builder


@router.callback_query(F.data.startswith("pay_currency|"))
async def handle_pay_currency(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    currency = callback_query.data.split("|")[1]

    if currency == "STARS":
        return await process_callback_pay_stars(callback_query, state, session)

    base_builder = await _build_pay_menu_for_currency(currency)
    module_buttons = await run_hooks(
        "pay_menu_buttons",
        chat_id=callback_query.from_user.id,
        admin=False,
        session=session,
    )
    builder = insert_hook_buttons(base_builder, module_buttons)

    donations_enabled = bool(BUTTONS_CONFIG.get("DONATIONS_BUTTON_ENABLE", DONATIONS_ENABLE))
    if donations_enabled:
        builder.row(InlineKeyboardButton(text=btn.DONAT_BUTTON, callback_data="donate"))

    builder.row(InlineKeyboardButton(text=btn.BACK, callback_data="back_to_currency"))
    builder.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=PAYMENT_METHODS_MSG,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "balance")
async def balance_handler(callback_query: CallbackQuery, session: AsyncSession):
    stmt = select(User.balance).where(User.tg_id == callback_query.from_user.id)
    result = await session.execute(stmt)
    balance_rub = result.scalar_one_or_none() or 0.0

    language_code = getattr(callback_query.from_user, "language_code", None)
    balance_text = await format_for_user(session, callback_query.from_user.id, balance_rub, language_code)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=btn.PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=btn.BALANCE_HISTORY, callback_data="balance_history"))
    if BUTTONS_CONFIG.get("COUPON_BUTTON_ENABLE", True):
        builder.row(InlineKeyboardButton(text=btn.COUPON, callback_data="activate_coupon"))
    builder.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    text = BALANCE_MANAGEMENT_TEXT.format(balance=balance_text)
    image_path = os.path.join("img", "pay.jpg")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        disable_web_page_preview=False,
    )


@router.callback_query(F.data == "balance_history")
async def balance_history_handler(callback_query: CallbackQuery, session: Any):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=btn.PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    records = await get_last_payments(session, callback_query.from_user.id, statuses=["success"])

    if records:
        language_code = getattr(callback_query.from_user, "language_code", None)
        history_text = f"{BALANCE_HISTORY_HEADER}</b>\n\n<blockquote>"
        for record in records:
            amount_rub = record["amount"] or 0
            formatted_amount = await format_for_user(session, callback_query.from_user.id, amount_rub, language_code)
            payment_system = record["payment_system"]
            status = record["status"]
            date = record["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            history_text += f"Сумма: {formatted_amount}\nОплата: {payment_system}\nСтатус: {status}\nДата: {date}\n\n"
        history_text += "</blockquote>"
    else:
        history_text = "❌ У вас пока нет операций с балансом."

    await edit_or_send_message(
        target_message=callback_query.message,
        text=history_text,
        reply_markup=builder.as_markup(),
        media_path=None,
        disable_web_page_preview=False,
    )


@router.callback_query(F.data == "back_to_currency")
async def back_to_currency(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    providers_config = await get_payment_providers_config()
    providers_with_hooks = await get_providers_with_hooks(providers_config)

    show_stars = bool((providers_with_hooks.get("STARS") or {}).get("enabled"))
    show_tribute = bool((providers_with_hooks.get("TRIBUTE") or {}).get("enabled"))
    keyboard = build_currency_choice_kb(show_stars=show_stars, prefix="pay_currency", show_tribute=show_tribute)
    keyboard.row(InlineKeyboardButton(text=btn.BACK, callback_data="back_to_pay"))
    await edit_or_send_message(
        target_message=callback_query.message,
        text=FAST_PAY_CHOOSE_CURRENCY,
        reply_markup=keyboard.as_markup(),
    )


@router.callback_query(F.data == "back_to_pay")
async def back_to_pay(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    return await balance_handler(callback_query, session)


@router.callback_query(F.data == "pay_tribute")
async def handle_pay_tribute(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_tribute(callback_query, state, session)
