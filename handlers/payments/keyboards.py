from collections.abc import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import RENEWAL_PRICES
from handlers.buttons import BACK, CUSTOM_AMOUNT
from handlers.payments.currency_rates import format_for_user


async def payment_options_for_user(
    db_session,
    tg_id: int,
    language_code: str | None,
    *,
    force_currency: str | None = None,  
) -> list[dict]:
    items = []
    for price_rub in RENEWAL_PRICES.values():
        txt = await format_for_user(
            db_session,
            tg_id,
            price_rub,
            language_code,
            force_currency=force_currency, 
        )
        items.append({"text": txt, "callback_data": f"amount|{int(price_rub)}"})
    return items


def payment_options(currency: str = "RUB") -> list[dict]:
    return [{"text": f"{price} {currency}", "callback_data": f"amount|{price}"} for price in RENEWAL_PRICES.values()]


def build_amounts_keyboard(
    *,
    prefix: str,
    pattern: str,
    back_cb: str = "balance",
    custom_cb: str | tuple[str, str] | None = None,
    per_row: int = 2,
    opts: Iterable[dict] | None = None,
) -> InlineKeyboardMarkup:
    items = list(opts) if opts is not None else payment_options()
    b = InlineKeyboardBuilder()
    row = []
    for i, item in enumerate(items, 1):
        row.append(
            InlineKeyboardButton(
                text=item["text"],
                callback_data=pattern.format(prefix=prefix, price=item["callback_data"].split("|", 1)[-1]),
            )
        )
        if i % per_row == 0:
            b.row(*row)
            row = []
    if row:
        b.row(*row)
    if custom_cb:
        cb = custom_cb[1] if isinstance(custom_cb, tuple) else custom_cb
        b.row(InlineKeyboardButton(text=CUSTOM_AMOUNT, callback_data=cb))
    b.row(InlineKeyboardButton(text=BACK, callback_data=back_cb))
    return b.as_markup()


def parse_amount_from_callback(data: str, *, prefixes: list[str]) -> int | None:
    for p in prefixes:
        if data.startswith(f"{p}_amount|"):
            try:
                return int(data.split("|", 1)[1])
            except Exception:
                return None
        if data.startswith(f"{p}|amount|"):
            try:
                return int(data.rsplit("|", 1)[-1])
            except Exception:
                return None
        if data.startswith(f"{p}|"):
            try:
                return int(data.split("|", 1)[1])
            except Exception:
                return None
    return None


def pay_keyboard(url: str, *, pay_text: str, back_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=pay_text, url=url))
    b.row(InlineKeyboardButton(text=BACK, callback_data=back_cb))
    return b.as_markup()


def back_keyboard(back_cb: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text=BACK, callback_data=back_cb))
    return b.as_markup()
