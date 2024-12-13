from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPPORT_CHAT_URL, CONNECT_IOS, CONNECT_WINDOWS


def build_instructions_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="💬 Поддержка",
        url=SUPPORT_CHAT_URL
    )
    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


def build_connect_pc_kb(key: str):
    builder = InlineKeyboardBuilder()

    builder.button(
        text="💻 Подключить Windows",
        url=f"{CONNECT_WINDOWS}{key}",
    )
    builder.button(
        text="💻 Подключить MacOS",
        url=f"{CONNECT_IOS}{key}",
    )
    builder.button(
        text="🆘 Поддержка",
        url=f"{SUPPORT_CHAT_URL}",
    )
    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile",
    )

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
