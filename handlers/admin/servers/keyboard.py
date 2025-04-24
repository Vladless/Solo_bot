from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.buttons import BACK


class AdminServerCallback(CallbackData, prefix="admin_server"):
    action: str
    data: str


def build_manage_server_kb(server_name: str, cluster_name: str, enabled: bool) -> InlineKeyboardMarkup:
    from ..clusters.keyboard import AdminClusterCallback

    builder = InlineKeyboardBuilder()

    toggle_text = "🔴 Отключить" if enabled else "🟢 Включить"
    toggle_action = "disable" if enabled else "enable"

    builder.button(text=toggle_text, callback_data=AdminServerCallback(action=toggle_action, data=server_name).pack())

    builder.button(
        text="📈 Задать лимит", callback_data=AdminServerCallback(action="set_limit", data=server_name).pack()
    )

    builder.button(text="🗑️ Удалить", callback_data=AdminServerCallback(action="delete", data=server_name).pack())

    builder.button(
        text="✏️ Сменить название", callback_data=AdminServerCallback(action="rename", data=server_name).pack()
    )

    builder.button(text=BACK, callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack())

    builder.adjust(1)
    return builder.as_markup()
