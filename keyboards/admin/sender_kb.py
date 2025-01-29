from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.admin.panel_kb import build_admin_back_btn


class AdminSenderCallback(CallbackData, prefix="admin_sender"):
    type: str


def build_sender_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Все пользователи", callback_data=AdminSenderCallback(type="all").pack())
    builder.button(text="✅ Пользователи с подпиской", callback_data=AdminSenderCallback(type="subscribed").pack())
    builder.button(text="❌ Пользователи без подписки", callback_data=AdminSenderCallback(type="unsubscribed").pack())
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()
