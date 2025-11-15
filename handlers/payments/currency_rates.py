from __future__ import annotations

import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional, Tuple

import aiohttp
import sqlalchemy as sa

from config import FX_MARKUP as DEFAULT_FX_MARKUP
from config import RUB_TO_USD as DEFAULT_RUB_TO_USD
from core.bootstrap import MONEY_CONFIG


CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
CACHE_TTL = 60 * 30

cache: dict[str, tuple[float, Decimal]] = {}


def _q(x: Decimal, prec: int = 8) -> Decimal:
    return x.quantize(Decimal(10) ** -prec, rounding=ROUND_HALF_UP)


def _round2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def to_rub(amount: float | Decimal, base: str, *, session: aiohttp.ClientSession | None = None) -> Decimal:
    """
    Переводит сумму ИЗ валюты base В РУБЛИ.
    Использует get_rub_rate(base): base_per_rub, т.е. сколько единиц base в 1 рубле.
    RUB = amount / base_per_rub.
    """
    rate = await get_rub_rate(base, session=session)
    return _q(Decimal(amount) / rate, prec=2)


async def get_rub_rate(quote: str, *, session: aiohttp.ClientSession | None = None) -> Decimal:
    code = quote.upper()
    if code == "RUB":
        return Decimal("1")

    rub_to_usd_cfg = MONEY_CONFIG.get("RUB_TO_USD", DEFAULT_RUB_TO_USD)
    rub_to_usd_value = 0.0
    if rub_to_usd_cfg not in (False, None, 0):
        try:
            rub_to_usd_value = float(rub_to_usd_cfg)
        except (TypeError, ValueError):
            rub_to_usd_value = 0.0

    if code == "USD" and rub_to_usd_value > 0:
        rate = _q(Decimal("1") / Decimal(str(rub_to_usd_value)))
        cache[code] = (time.time(), rate)
        return rate

    now = time.time()
    cached = cache.get(code)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    owns = False
    s = session
    if s is None:
        s = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        owns = True
    try:
        async with s.get(CBR_URL, headers={"Accept": "application/json"}) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    finally:
        if owns and not s.closed:
            await s.close()

    valutes = data.get("Valute") or {}
    v = valutes.get(code)
    if not v:
        raise ValueError(f"Валюта {code} не найдена у ЦБ")

    rub_per_unit = Decimal(str(v["Value"])) / Decimal(str(v.get("Nominal", 1)))
    rate = _q(Decimal("1") / rub_per_unit)

    fx_markup_cfg = MONEY_CONFIG.get("FX_MARKUP", DEFAULT_FX_MARKUP)
    try:
        fx_markup_value = Decimal(str(fx_markup_cfg))
    except (TypeError, ValueError):
        fx_markup_value = Decimal("0")

    if code != "RUB" and fx_markup_value:
        pct = fx_markup_value / Decimal("100")
        rate = _q(rate * (Decimal("1") + pct))

    cache[code] = (now, rate)
    return rate


async def convert_from_rub(
    amount_rub: Decimal | float,
    to_ccy: str,
    *,
    session: aiohttp.ClientSession | None = None,
) -> Decimal:
    """
    Конвертирует сумму из RUB в валюту to_ccy, используя get_rub_rate(to_ccy).
    """
    amt = Decimal(str(amount_rub))
    ccy = to_ccy.upper()
    if ccy == "RUB":
        return _round2(amt)
    rate = await get_rub_rate(ccy, session=session)
    val = amt * rate
    return _round2(val)


def pick_currency(
    language_code: str | None,
    user_currency: str | None = None,
    force_currency: str | None = None,
) -> str:
    if force_currency in {"USD", "RUB"}:
        return force_currency

    mode_cfg = MONEY_CONFIG.get("CURRENCY_MODE", "RUB")
    mode = str(mode_cfg or "RUB").upper()
    if mode not in {"RUB", "USD", "RUB+USD"}:
        mode = "RUB"

    if mode == "RUB":
        return "RUB"
    if mode == "USD":
        return "USD"

    if user_currency in {"USD", "RUB"}:
        return user_currency

    code = (language_code or "").split("-")[0].lower()
    return "RUB" if code == "ru" else "USD"


def fmt_money(amount: Decimal, currency: str, language_code: str | None) -> str:
    q = _round2(amount)
    if currency == "USD":
        s = f"{q:,.2f}"
        if (language_code or "").startswith("ru"):
            s = s.replace(",", " ")
        return f"${s}"
    s = f"{q:,.2f}".replace(",", " ")
    return f"{s} ₽"


async def display_price(
    amount_rub: Decimal | float,
    language_code: str | None,
    *,
    user_currency: str | None = None,
    force_currency: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> tuple[str, str, Decimal]:
    cur = pick_currency(language_code, user_currency=user_currency, force_currency=force_currency)

    if cur == "RUB":
        val = _round2(Decimal(str(amount_rub)))
    else:
        val = await convert_from_rub(Decimal(str(amount_rub)), cur, session=session)

    txt = fmt_money(val, cur, language_code)
    return txt, cur, val


async def money_for_user(
    db_session,
    tg_id: int,
    amount_rub: float | int | Decimal,
    language_code: Optional[str],
    force_currency: Optional[str] = None,
) -> Tuple[str, str, Decimal]:
    """
    Возвращает: (text, currency, value)
    text: строка для показа пользователю, например "$12.34" или "1 234.00 ₽"
    currency: "USD" или "RUB"
    value: Decimal в выбранной валюте
    """
    row = await db_session.execute(
        sa.text("select preferred_currency from users where tg_id = :id"),
        {"id": tg_id},
    )
    user_currency = row.scalar()

    txt, cur, val = await display_price(
        amount_rub,
        language_code,
        user_currency=user_currency,
        force_currency=force_currency,
        session=None,
    )
    return txt, cur, val


async def format_for_user(
    db_session,
    tg_id: int,
    amount_rub: float | int | Decimal,
    language_code: Optional[str],
    force_currency: Optional[str] = None,
) -> str:
    text, _, _ = await money_for_user(
        db_session,
        tg_id,
        amount_rub,
        language_code,
        force_currency=force_currency,
    )
    return text
