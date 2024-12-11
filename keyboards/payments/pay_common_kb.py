from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.texts import PAYMENT_OPTIONS


def build_payment_kb(callback: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for i in range(0, len(PAYMENT_OPTIONS), 2):
        if i + 1 < len(PAYMENT_OPTIONS):
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'{callback}_{PAYMENT_OPTIONS[i]["callback_data"]}',
                ),
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i + 1]["text"],
                    callback_data=f'{callback}_{PAYMENT_OPTIONS[i + 1]["callback_data"]}',
                ),
            )
        else:
            builder.row(
                InlineKeyboardButton(
                    text=PAYMENT_OPTIONS[i]["text"],
                    callback_data=f'{callback}_{PAYMENT_OPTIONS[i]["callback_data"]}',
                )
            )

    builder.row(
        InlineKeyboardButton(
            text="💰 Ввести свою сумму",
            callback_data=f"enter_custom_amount_{callback}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="pay"
        )
    )

    return builder.as_markup()
