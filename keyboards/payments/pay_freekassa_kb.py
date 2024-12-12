from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_fk_invoice_kb(amount: int, payment_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=f"Оплатить {amount} рублей",
        url=payment_url,
    )
    builder.button(
        text="⬅️ Назад",
        callback_data="pay",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_fk_pay_kb(payment_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=f"Оплатить",
        url=payment_url,
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
