from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CHANNEL_URL, SUPPORT_CHAT_URL


class StartCommandCallback(CallbackData, prefix='start_command'):
    page: str
    reload: bool = False


def build_start_kb(trial_status: int, is_admin: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Check if trial status is 0
    if trial_status == 0:
        builder.row(
            InlineKeyboardButton(
                text="🔗 Подключить VPN",
                callback_data="connect_vpn",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="👤 Личный кабинет",
            callback_data="profile",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📞 Поддержка",
            url=SUPPORT_CHAT_URL,
        ),
        InlineKeyboardButton(
            text="📢 Канал",
            url=CHANNEL_URL,
        ),
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
            text="🌐 О нашем VPN",
            callback_data="about_vpn",
        )
    )

    return builder.as_markup()
