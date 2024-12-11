from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_profile_kb(is_admin: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="➕ Устройство",
            callback_data="create_key",
        ),
        InlineKeyboardButton(
            text="📱 Мои устройства",
            callback_data="view_keys",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="💳 Пополнить баланс",
            callback_data="pay",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="👥 Пригласить друзей",
            callback_data="invite",
        ),
        InlineKeyboardButton(
            text="📘 Инструкции",
            callback_data="instructions",
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="💡 Тарифы",
            callback_data="view_tariffs",
        )
    )

    # Check if user is admin
    if is_admin:
        builder.row(
            InlineKeyboardButton(
                text="🔧 Администратор",
                callback_data="admin",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Главное меню",
            callback_data="start",
        )
    )

    return builder.as_markup()


def build_profile_back_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile"
    )
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
