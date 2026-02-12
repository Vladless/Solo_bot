from typing import Any

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from handlers.payments.currency_rates import format_for_user


async def add_tariff_button_generic(
    builder: InlineKeyboardBuilder,
    tariff: dict[str, Any],
    session: AsyncSession,
    tg_id: int,
    language_code: str | None,
    callback_prefix: str,
):
    """Добавляет кнопку тарифа с учётом конфигуратора."""
    is_configurable = bool(tariff.get("configurable"))
    if is_configurable:
        button_text = tariff["name"]
    else:
        price_rub = float(tariff.get("price_rub") or 0)
        price_text = await format_for_user(session, tg_id, price_rub, language_code)
        button_text = f"{tariff['name']} — {price_text}"

    builder.row(
        InlineKeyboardButton(
            text=button_text,
            callback_data=f"{callback_prefix}|{tariff['id']}",
        )
    )
