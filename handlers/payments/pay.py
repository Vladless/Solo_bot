import os
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import MULTICURRENCY_ENABLE, DONATIONS_ENABLE, PROVIDERS_ENABLED
from handlers.texts import FAST_PAY_CHOOSE_CURRENCY, BALANCE_MANAGEMENT_TEXT, PAYMENT_METHODS_MSG

from database import get_last_payments
from database.models import User
from handlers import buttons as btn

from handlers.payments.stars.handlers import process_callback_pay_stars
from handlers.payments.tribute.handlers import process_callback_pay_tribute
from handlers.payments.wata.wata import process_callback_pay_wata
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from handlers.payments.currency_rates import format_for_user
from handlers.payments.currency_flow import build_currency_choice_kb
from handlers.payments.providers import get_providers_with_hooks

from ..utils import edit_or_send_message

router = Router()


@router.callback_query(F.data == "pay")
async def handle_pay(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    PROVIDERS = await get_providers_with_hooks(PROVIDERS_ENABLED)
    payment_handlers = []

    for key, cfg in PROVIDERS.items():
        if not cfg.get("enabled"):
            continue
        val = cfg.get("value")
        if not val:
            continue
        fn = globals().get(f"process_callback_{val}")
        if callable(fn):
            payment_handlers.append(fn)

    module_buttons = await run_hooks("pay_menu_buttons", chat_id=callback_query.from_user.id, admin=False, session=session)
    has_extra_menu_items = bool(module_buttons) or bool(DONATIONS_ENABLE) or bool((PROVIDERS.get("TRIBUTE") or {}).get("enabled"))

    if MULTICURRENCY_ENABLE:
        show_stars = bool((PROVIDERS.get("STARS") or {}).get("enabled"))
        show_tribute = bool((PROVIDERS.get("TRIBUTE") or {}).get("enabled"))
        kb = build_currency_choice_kb(show_stars=show_stars, prefix="pay_currency", show_tribute=show_tribute)
        await edit_or_send_message(
            target_message=callback_query.message,
            text=FAST_PAY_CHOOSE_CURRENCY,
            reply_markup=kb.as_markup()
        )
        return

    if not has_extra_menu_items:
        enabled_providers_count = sum(1 for _k, _cfg in PROVIDERS.items() if _cfg.get("enabled"))
        if enabled_providers_count == 1 and len(payment_handlers) == 1:
            return await payment_handlers[0](callback_query, state, session)

    builder = InlineKeyboardBuilder()
    for key, cfg in PROVIDERS.items():
        if not cfg.get("enabled"):
            continue
        text = getattr(btn, key, key)
        builder.row(InlineKeyboardButton(text=text, callback_data=cfg["value"]))

    if DONATIONS_ENABLE:
        builder.row(InlineKeyboardButton(text=btn.DONAT_BUTTON, callback_data="donate"))

    builder = insert_hook_buttons(builder, module_buttons)
    builder.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(target_message=callback_query.message, text=PAYMENT_METHODS_MSG, reply_markup=builder.as_markup())


async def _build_pay_menu_for_currency(currency: str) -> InlineKeyboardBuilder:
    PROVIDERS = await get_providers_with_hooks(PROVIDERS_ENABLED)
    b = InlineKeyboardBuilder()
    for key, cfg in PROVIDERS.items():
        if not cfg.get("enabled"):
            continue
        if cfg.get("currency") != currency:
            continue
        text = getattr(btn, key, key)
        b.row(InlineKeyboardButton(text=text, callback_data=cfg["value"]))
    return b


@router.callback_query(F.data.startswith("pay_currency|"))
async def handle_pay_currency(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    currency = callback_query.data.split("|")[1]

    if currency == "STARS":
        return await process_callback_pay_stars(callback_query, state, session)

    base_builder = await _build_pay_menu_for_currency(currency)
    module_buttons = await run_hooks("pay_menu_buttons", chat_id=callback_query.from_user.id, admin=False, session=session)
    builder = insert_hook_buttons(base_builder, module_buttons)

    if DONATIONS_ENABLE:
        builder.row(InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate"))

    builder.row(InlineKeyboardButton(text=btn.BACK, callback_data="back_to_currency"))
    builder.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(target_message=callback_query.message, text=PAYMENT_METHODS_MSG, reply_markup=builder.as_markup())


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
    builder.row(InlineKeyboardButton(text=btn.COUPON, callback_data="activate_coupon"))
    builder.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    text = BALANCE_MANAGEMENT_TEXT.format(balance=balance_text)
    image_path = os.path.join("img", "pay.jpg")

    await edit_or_send_message(target_message=callback_query.message, text=text, reply_markup=builder.as_markup(), media_path=image_path, disable_web_page_preview=False)


@router.callback_query(F.data == "balance_history")
async def balance_history_handler(callback_query: CallbackQuery, session: Any):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=btn.PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=btn.MAIN_MENU, callback_data="profile"))

    records = await get_last_payments(session, callback_query.from_user.id, statuses=["success"])

    if records:
        language_code = getattr(callback_query.from_user, "language_code", None)
        history_text = "<b>💳 История операций:</b>\n\n<blockquote>"
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

    await edit_or_send_message(target_message=callback_query.message, text=history_text, reply_markup=builder.as_markup(), media_path=None, disable_web_page_preview=False)


@router.callback_query(F.data == "back_to_currency")
async def back_to_currency(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    PROVIDERS = await get_providers_with_hooks(PROVIDERS_ENABLED)
    show_stars = bool((PROVIDERS.get("STARS") or {}).get("enabled"))
    show_tribute = bool((PROVIDERS.get("TRIBUTE") or {}).get("enabled"))
    kb = build_currency_choice_kb(show_stars=show_stars, prefix="pay_currency", show_tribute=show_tribute)
    kb.row(InlineKeyboardButton(text=btn.BACK, callback_data="back_to_pay"))
    await edit_or_send_message(
        target_message=callback_query.message,
        text=FAST_PAY_CHOOSE_CURRENCY,
        reply_markup=kb.as_markup()
    )


@router.callback_query(F.data == "back_to_pay")
async def back_to_pay(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    return await balance_handler(callback_query, session)


@router.callback_query(F.data == "pay_wata_ru")
async def handle_pay_wata_ru(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_wata(callback_query, state, session, cassa_name="ru")


@router.callback_query(F.data == "pay_wata_sbp")
async def handle_pay_wata_sbp(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_wata(callback_query, state, session, cassa_name="sbp")


@router.callback_query(F.data == "pay_wata_int")
async def handle_pay_wata_int(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_wata(callback_query, state, session, cassa_name="int")

@router.callback_query(F.data == "pay_tribute")
async def handle_pay_tribute(callback_query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await process_callback_pay_tribute(callback_query, state, session)
