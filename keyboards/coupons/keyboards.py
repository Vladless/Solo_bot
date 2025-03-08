from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_coupon_keyboard() -> InlineKeyboardBuilder:
    """
    Создает клавиатуру для работы с купонами.

    Returns:
        InlineKeyboardBuilder: Построитель клавиатуры с кнопками для купонов.
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))
    return builder
