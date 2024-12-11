from aiocryptopay.models.invoice import Invoice
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_invoice_kb(invoice: Invoice) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Пополнить",
        url=invoice.bot_invoice_url,
    )
    builder.button(
        text="⬅️ Назад",
        callback_data="pay",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
