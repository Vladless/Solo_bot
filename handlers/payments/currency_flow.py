from typing import Iterable, List, Any
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.texts import FAST_PAY_NOT_ENOUGH
from handlers.buttons import RUB_CURRENCY, USD_CURRENCY, STARS, MAIN_MENU
from config import TRIBUTE_LINK
from .currency_rates import format_for_user


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
    trib_enabled = (bool(trib) if show_tribute is None else bool(show_tribute) and bool(trib))

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
    if not isinstance(required_amount, (int, float)) or required_amount <= 0:
        return "💳"
    amount_txt = await format_for_user(
        session, tg_id, float(required_amount), language_code, force_currency=force_currency
    )
    return FAST_PAY_NOT_ENOUGH.format(amount=amount_txt)


def filter_providers_by_currency(
    currency: str,
    providers: Iterable[str],
    rub_providers: Iterable[str],
) -> List[str]:
    rub_set = {p.upper() for p in rub_providers}
    out: List[str] = []
    for p in providers:
        up = p.upper()
        if currency == "RUB":
            if up in rub_set or up == "WATA":
                out.append(p)
        elif currency == "USD":
            if (up not in rub_set or up == "WATA") and up != "STARS":
                out.append(p)
        elif currency == "STARS":
            if up == "STARS":
                out.append(p)
        else:
            out.append(p)
    return out


def currency_for_provider(up_provider: str, rub_providers: Iterable[str]) -> str | None:
    if up_provider in {p.upper() for p in rub_providers}:
        return "RUB"
    if up_provider == "STARS":
        return "STARS"
    if up_provider == "WATA":
        return None
    return "USD"


def currency_label(code: str) -> str:
    if code == "RUB":
        return "RUB"
    if code == "USD":
        return "USD/Cryptowallet"
    if code == "STARS":
        return "Telegram Stars"
    return code
