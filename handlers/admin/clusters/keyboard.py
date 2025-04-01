from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.buttons import BACK

from ..panel.keyboard import build_admin_back_btn
from ..servers.keyboard import AdminServerCallback


class AdminClusterCallback(CallbackData, prefix="admin_cluster"):
    action: str
    data: str | None = None


def build_clusters_editor_kb(servers: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    cluster_names = list(servers.keys())
    for i in range(0, len(cluster_names), 2):
        builder.row(*[
            InlineKeyboardButton(
                text=f"⚙️ {name}",
                callback_data=AdminClusterCallback(action="manage", data=name).pack(),
            )
            for name in cluster_names[i : i + 2]
        ])

    builder.row(
        InlineKeyboardButton(text="➕ Добавить кластер", callback_data=AdminClusterCallback(action="add").pack())
    )

    builder.row(build_admin_back_btn())

    return builder.as_markup()


def build_manage_cluster_kb(cluster_servers: list, cluster_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for server in cluster_servers:
        builder.row(
            InlineKeyboardButton(
                text=f"🌍 {server['server_name']}",
                callback_data=AdminServerCallback(action="manage", data=server["server_name"]).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить сервер",
            callback_data=AdminServerCallback(action="add", data=cluster_name).pack(),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🛠 Управление",
            callback_data=AdminClusterCallback(action="manage_cluster", data=cluster_name).pack(),
        )
    )

    builder.row(build_admin_back_btn("clusters"))
    return builder.as_markup()


def build_cluster_management_kb(cluster_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🌐 Доступность",
            callback_data=AdminClusterCallback(action="availability", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔄 Синхронизация",
            callback_data=AdminClusterCallback(action="sync", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="💾 Создать бэкап",
            callback_data=AdminClusterCallback(action="backup", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⏳ Добавить время",
            callback_data=AdminClusterCallback(action="add_time", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Сменить название",
            callback_data=AdminClusterCallback(action="rename", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack(),
        )
    )

    return builder.as_markup()


def build_sync_cluster_kb(cluster_servers: list, cluster_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for server in cluster_servers:
        builder.row(
            InlineKeyboardButton(
                text=f"🔄 Синхронизировать {server['server_name']}",
                callback_data=AdminClusterCallback(action="sync-server", data=server["server_name"]).pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="📍 Синхронизировать кластер",
            callback_data=AdminClusterCallback(action="sync-cluster", data=cluster_name).pack(),
        )
    )

    builder.row(build_admin_back_btn("clusters"))

    return builder.as_markup()
