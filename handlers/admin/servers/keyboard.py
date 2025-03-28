from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.buttons import BACK


class AdminServerCallback(CallbackData, prefix="admin_server"):
    action: str
    data: str


def build_manage_server_kb(server_name: str, cluster_name: str) -> InlineKeyboardMarkup:
    from ..clusters.keyboard import AdminClusterCallback

    builder = InlineKeyboardBuilder()
    builder.button(text="🗑️ Удалить", callback_data=AdminServerCallback(action="delete", data=server_name).pack())
    builder.button(text=BACK, callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack())
    builder.adjust(1)
    return builder.as_markup()


def build_delete_server_kb(server_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data=AdminServerCallback(action="delete_confirm", data=server_name).pack())
    builder.button(text=BACK, callback_data=AdminServerCallback(action="manage", data=server_name).pack())
    builder.adjust(1)
    return builder.as_markup()
