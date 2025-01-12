from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_btn


class AdminServerEditorCallback(CallbackData, prefix='admin_server_editor'):
    action: str
    data: str


def build_clusters_editor_kb(servers: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for cluster_name in servers:
        builder.button(
            text=f"⚙️ {cluster_name}",
            callback_data=AdminServerEditorCallback(
                action="clusters_manage",
                data=cluster_name
            ).pack()
        )

    builder.button(
        text="➕ Добавить кластер",
        callback_data=AdminPanelCallback(action="clusters_add").pack()
    )
    builder.row(
        build_admin_back_btn("admin")
    )
    return builder.as_markup()


def build_manage_cluster_kb(cluster_servers, cluster_name) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for server in cluster_servers:
        builder.button(
            text=f"🌍 {server['server_name']}",
            callback_data=AdminServerEditorCallback(
                action="servers_manage",
                data=server["server_name"]
            ).pack()
        )

    builder.button(
        text="➕ Добавить сервер",
        callback_data=AdminServerEditorCallback(
            action="servers_add",
            data=cluster_name
        ).pack()
    )
    builder.button(
        text="🌐 Доступность серверов",
        callback_data=AdminServerEditorCallback(
            action="servers_availability",
            data=cluster_name
        ).pack()
    )
    builder.button(
        text="💾 Создать бэкап кластера",
        callback_data=AdminServerEditorCallback(
            action="clusters_backup",
            data=cluster_name
        ).pack()
    )
    builder.row(
        build_admin_back_btn("servers_editor")
    )
    return builder.as_markup()


def build_manage_server_kb(server_name: str, cluster_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗑️ Удалить",
        callback_data=AdminServerEditorCallback(
            action="servers_delete",
            data=server_name
        ).pack()
    )
    builder.button(
        text="🔙 Назад",
        callback_data=AdminServerEditorCallback(
            action="clusters_manage",
            data=cluster_name
        ).pack()
    )
    return builder.as_markup()


def build_delete_server_kb(server_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да",
        callback_data=AdminServerEditorCallback(
            action="servers_delete_confirm",
            data=server_name
        ).pack()
    )
    builder.button(
        text="🔙 Назад",
        callback_data=AdminServerEditorCallback(
            action="servers_manage",
            data=server_name
        ).pack()
    )
    return builder.as_markup()


def build_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data="servers_editor"
        )
    )
    return builder.as_markup()
