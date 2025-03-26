from aiogram.types import InlineKeyboardMarkup


def build_notification_kb(email: str) -> InlineKeyboardMarkup:
    """
    Формирует inline-клавиатуру для уведомлений.
    Кнопки: "🔄 Продлить VPN" (callback_data содержит email) и "👤 Личный кабинет".
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Продлить VPN", callback_data=f"renew_key|{email}")
    builder.button(text="👤 Личный кабинет", callback_data="profile")
    builder.adjust(1)
    return builder.as_markup()


def build_notification_expired_kb() -> InlineKeyboardMarkup:
    """
    Формирует inline-клавиатуру для уведомлений после удаления или продления.
    Кнопка: "👤 Личный кабинет"
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Личный кабинет", callback_data="profile")
    return builder.as_markup()
