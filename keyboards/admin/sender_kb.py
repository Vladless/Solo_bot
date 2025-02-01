from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.admin.panel_kb import build_admin_back_btn


class AdminSenderCallback(CallbackData, prefix="admin_sender"):
    type: str
    data: str | None = None


def build_sender_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Все пользователи", callback_data=AdminSenderCallback(type="all").pack())
    builder.button(text="✅ Пользователи с подпиской", callback_data=AdminSenderCallback(type="subscribed").pack())
    builder.button(text="❌ Пользователи без подписки", callback_data=AdminSenderCallback(type="unsubscribed").pack())
    builder.button(text="📢 Пользователи кластера", callback_data=AdminSenderCallback(type="cluster-select").pack())
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_clusters_kb(clusters: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cluster in clusters:
        name = cluster["cluster_name"]
        builder.button(text=f"🌐 {name}", callback_data=AdminSenderCallback(type="cluster", data=name).pack())

    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()
