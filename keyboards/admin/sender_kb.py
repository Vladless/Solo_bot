from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.admin.panel_kb import build_admin_back_btn


class AdminSenderCallback(CallbackData, prefix="admin_sender"):
    type: str


def build_sender_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📢 Отправить всем",
        callback_data=AdminSenderCallback(
            type="all"
        ).pack()
    )
    builder.button(
        text="📢 Отправить с подпиской",
        callback_data=AdminSenderCallback(
            type="subscribed"
        ).pack()
    )
    builder.button(
        text="📢 Отправить без подписки",
        callback_data=AdminSenderCallback(
            type="unsubscribed"
        ).pack()
    )
    builder.row(
        build_admin_back_btn("admin")
    )
    builder.adjust(1)
    return builder.as_markup()
