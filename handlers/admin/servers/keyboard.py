from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from settings.buttons import BACK


class AdminServerCallback(CallbackData, prefix="admin_server"):
    action: str
    data: str


def build_manage_server_kb(server_name: str, cluster_name: str, enabled: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    toggle_text = "🔴 Отключить" if enabled else "🟢 Включить"
    toggle_action = "disable" if enabled else "enable"

    builder.button(
        text=toggle_text,
        callback_data=AdminServerCallback(action=toggle_action, data=server_name).pack(),
    )

    builder.button(
        text="📈 Лимит",
        callback_data=AdminServerCallback(action="set_limit", data=server_name).pack(),
    )

    builder.button(
        text="🗑️ Удалить",
        callback_data=AdminServerCallback(action="delete", data=server_name).pack(),
    )

    builder.button(
        text="✏️ Правка",
        callback_data=f"edit_server|{server_name}",
    )

    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"cluster_servers|{cluster_name}"))
    return builder.as_markup()


def build_edit_server_fields_kb(server_name: str, server_data: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(text="📝 Имя", callback_data=f"edit_server_field|{server_name}|server_name")
    builder.button(text="🗂 Кластер", callback_data=f"edit_server_field|{server_name}|cluster_name")
    builder.button(text="🌐 API URL", callback_data=f"edit_server_field|{server_name}|api_url")
    if server_data.get("subscription_url"):
        builder.button(text="📡 Подписка", callback_data=f"edit_server_field|{server_name}|subscription_url")
    builder.button(text="🔑 Inbound", callback_data=f"edit_server_field|{server_name}|inbound_id")
    builder.button(text="⚙️ Тип панели", callback_data=f"select_panel_type|{server_name}")
    builder.adjust(2)
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data=AdminServerCallback(action="manage", data=server_name).pack())
    )

    return builder.as_markup()


def build_panel_type_selection_kb(server_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌐 3x-ui", callback_data=f"set_panel_type|{server_name}|3x-ui"),
                InlineKeyboardButton(text="🌀 remnawave", callback_data=f"set_panel_type|{server_name}|remnawave"),
            ],
            [InlineKeyboardButton(text=BACK, callback_data=f"edit_server|{server_name}")],
        ]
    )


def build_cluster_selection_kb(server_name: str, clusters: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for cluster in clusters:
        builder.row(InlineKeyboardButton(text=cluster, callback_data=f"set_cluster|{server_name}|{cluster}"))

    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"edit_server|{server_name}"))

    return builder.as_markup()


def build_cancel_edit_kb(server_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить", callback_data=f"edit_server|{server_name}")]]
    )
