from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_notification_kb(email: str, main_menu_text: str, renew_key_text: str) -> InlineKeyboardMarkup:
    """
    Формирует inline-клавиатуру для уведомлений.
    Кнопки: "🔄 Продлить VPN" (callback_data содержит email) и "👤 Личный кабинет".
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text=renew_key_text, callback_data=f"renew_key|{email}")
    builder.button(text=main_menu_text, callback_data="profile")
    builder.adjust(1)
    return builder.as_markup()


def build_notification_expired_kb(main_menu_text: str) -> InlineKeyboardMarkup:
    """
    Формирует inline-клавиатуру для уведомлений после удаления или продления.
    Кнопка: "👤 Личный кабинет"
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text=main_menu_text, callback_data="profile")
    return builder.as_markup()


def build_hot_lead_kb(discount_text: str, max_discount_text: str, final: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=max_discount_text if final else discount_text,
                    callback_data=(
                        "hot_lead_final_discount" if final else "hot_lead_discount"
                    ),
                )
            ]
        ]
    )


def build_tariffs_keyboard(
    tariffs: list[dict], prefix: str = "tariff"
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{t['name']} — {t['price_rub']}₽",
                callback_data=f"{prefix}|{t['id']}",
            )
        ]
        for t in tariffs
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)
