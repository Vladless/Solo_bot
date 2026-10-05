from typing import Any

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from services.payments.currency_rates import format_for_user
from settings.buttons import MAIN_MENU, RUB_CURRENCY, STARS, USD_CURRENCY
from settings.config import TRIBUTE_LINK
from settings.texts import FAST_PAY_NOT_ENOUGH


def build_currency_choice_kb(
    show_stars: bool,
    *,
    prefix: str = "choose_payment_currency",
    show_tribute: bool | None = None,
) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=RUB_CURRENCY, callback_data=f"{prefix}|RUB"))
    kb.row(InlineKeyboardButton(text=USD_CURRENCY, callback_data=f"{prefix}|USD"))
    trib = (TRIBUTE_LINK or "").strip()
    trib_enabled = bool(trib) if show_tribute is None else bool(show_tribute) and bool(trib)

    if show_stars:
        row = [InlineKeyboardButton(text=STARS, callback_data=f"{prefix}|STARS")]
        if trib_enabled:
            row.append(InlineKeyboardButton(text="TRIBUTE", url=trib))
        kb.row(*row)
    else:
        if trib_enabled:
            kb.row(InlineKeyboardButton(text="TRIBUTE", url=trib))

    kb.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
    return kb


async def shortfall_lead_text(
    session: Any,
    tg_id: int,
    required_amount: int | float | None,
    language_code: str | None,
    *,
    force_currency: str | None = None,
) -> str:
    if not isinstance(required_amount, int | float) or required_amount <= 0:
        return "💳"
    amount_txt = await format_for_user(
        session, tg_id, float(required_amount), language_code, force_currency=force_currency
    )
    return FAST_PAY_NOT_ENOUGH.format(amount=amount_txt)


def currency_label(code: str) -> str:
    if code == "RUB":
        return "RUB"
    if code == "USD":
        return "USD/Cryptowallet"
    if code == "STARS":
        return "Telegram Stars"
    return code
