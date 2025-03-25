from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import build_admin_back_btn
from ..servers.keyboard import AdminServerCallback


class AdminClusterCallback(CallbackData, prefix="admin_cluster"):
    action: str
    data: str


def build_clusters_editor_kb(servers: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    cluster_names = list(servers.keys())
    for i in range(0, len(cluster_names), 2):
        row_buttons = []
        for cluster_name in cluster_names[i : i + 2]:
            row_buttons.append(
                InlineKeyboardButton(
                    text=f"⚙️ {cluster_name}",
                    callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack(),
                )
            )
        builder.row(*row_buttons)

    builder.button(text="➕ Добавить кластер", callback_data=AdminClusterCallback(action="add").pack())
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def build_manage_cluster_kb(cluster_servers, cluster_name) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for server in cluster_servers:
        builder.button(
            text=f"🌍 {server['server_name']}",
            callback_data=AdminServerCallback(action="manage", data=server["server_name"]).pack(),
        )

    builder.button(
        text="➕ Добавить сервер",
        callback_data=AdminServerCallback(action="add", data=cluster_name).pack(),
    )
    builder.row(
        InlineKeyboardButton(
            text="🌐 Доступность",
            callback_data=AdminClusterCallback(action="availability", data=cluster_name).pack(),
        ),
        InlineKeyboardButton(
            text="🔄 Синхронизация",
            callback_data=AdminClusterCallback(action="sync", data=cluster_name).pack(),
        ),
    )
    builder.button(
        text="💾 Создать бэкап кластера",
        callback_data=AdminClusterCallback(action="backup", data=cluster_name).pack(),
    )
    builder.row(build_admin_back_btn("servers"))
    builder.adjust(1, 1, 1, 1, 1, 2, 1)
    return builder.as_markup()
