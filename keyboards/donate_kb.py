from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_donate_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🤖 Бот для покупки звезд",
        url="https://t.me/PremiumBot",
    )
    builder.button(
        text="💰 Ввести сумму доната",
        callback_data="enter_custom_donate_amount",
    )
    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_donate_amount_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="Задонатить",
        pay=True,
    )
    builder.button(
        text="⬅️ Назад",
        callback_data="donate",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_donate_back_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="⬅️ Назад",
        callback_data="donate",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
