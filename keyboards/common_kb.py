from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_back_kb(callback_data: str, text: str = "🔙 Назад") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=text,
        callback_data=callback_data,
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
