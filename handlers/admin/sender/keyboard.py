from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import build_admin_back_btn


class AdminSenderCallback(CallbackData, prefix="admin_sender"):
    type: str
    data: str | None = None


def build_sender_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(InlineKeyboardButton(text="👥 Все пользователи", callback_data=AdminSenderCallback(type="all").pack()))
    builder.row(
        InlineKeyboardButton(text="✅ С подпиской", callback_data=AdminSenderCallback(type="subscribed").pack()),
        InlineKeyboardButton(text="❌ Без подписки", callback_data=AdminSenderCallback(type="unsubscribed").pack()),
    )
    builder.row(
        InlineKeyboardButton(
            text="📍 Не использовавшие триал", callback_data=AdminSenderCallback(type="untrial").pack()
        )
    )
    builder.row(
        InlineKeyboardButton(text="📢 Кластер", callback_data=AdminSenderCallback(type="cluster-select").pack())
    )
    builder.row(build_admin_back_btn())

    return builder.as_markup()


def build_clusters_kb(clusters: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for cluster in clusters:
        name = cluster["cluster_name"]
        builder.button(text=f"🌐 {name}", callback_data=AdminSenderCallback(type="cluster", data=name).pack())

    builder.adjust(2)
    builder.row(build_admin_back_btn())

    return builder.as_markup()
