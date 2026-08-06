from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from settings.buttons import BACK

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn
from ..servers.keyboard import AdminServerCallback


class AdminClusterCallback(CallbackData, prefix="admin_cluster"):
    action: str
    data: str | None = None


def build_clusters_editor_kb(servers: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    cluster_names = list(servers.keys())
    for i in range(0, len(cluster_names), 2):
        row_buttons = []
        for name in cluster_names[i : i + 2]:
            servers_in_cluster = servers[name]
            all_disabled = all(not s["enabled"] for s in servers_in_cluster)
            label = f"❌ {name} (отключен)" if all_disabled else f"⚙️ {name}"
            row_buttons.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=AdminClusterCallback(action="manage", data=name).pack(),
                )
            )
        builder.row(*row_buttons)
    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить кластер",
            callback_data=AdminClusterCallback(action="add").pack(),
        )
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
            text="💸 Тариф",
            callback_data=AdminClusterCallback(action="attach_tariff_menu", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack(),
        )
    )

    return builder.as_markup()


def build_attach_tariff_kb(cluster_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="📋 Привязать тарифы",
            callback_data=AdminClusterCallback(action="set_subgroup", data=cluster_name).pack(),
        ),
        InlineKeyboardButton(
            text="🧹 Сбросить",
            callback_data=AdminClusterCallback(action="reset_cluster_subgroups", data=cluster_name).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text="🗂 Спецгруппы",
            callback_data=AdminClusterCallback(action="set_group", data=cluster_name).pack(),
        ),
        InlineKeyboardButton(
            text="🧹 Сбросить",
            callback_data=AdminClusterCallback(action="reset_cluster_groups", data=cluster_name).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack(),
        )
    )
    return builder.as_markup()


def build_legacy_reset_kb(cluster_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🧹 Сбросить привязки",
            callback_data=AdminClusterCallback(action="reset_cluster_subgroups", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminClusterCallback(action="attach_tariff_menu", data=cluster_name).pack(),
        )
    )
    return builder.as_markup()


def build_select_subgroup_servers_kb(
    cluster_name: str, cluster_servers: list, selected: set[str]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    names = []
    for s in cluster_servers:
        if isinstance(s, str):
            names.append(s)
        elif isinstance(s, dict):
            names.append(s.get("server_name") or s.get("name") or str(s))
        else:
            names.append(getattr(s, "server_name", None) or getattr(s, "name", None) or str(s))

    for i, name in enumerate(names):
        mark = "✅" if name in selected else "⬜️"
        builder.row(
            InlineKeyboardButton(
                text=f"{mark} {name}",
                callback_data=AdminClusterCallback(action="toggle_server_subgroup", data=f"{cluster_name}|{i}").pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="📋 Выбрать тарифы",
            callback_data=AdminClusterCallback(action="choose_subgroup", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="♻️ Сбросить выбор",
            callback_data=AdminClusterCallback(action="reset_subgroup_selection", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminClusterCallback(action="attach_tariff_menu", data=cluster_name).pack(),
        )
    )

    return builder.as_markup()


def build_tariff_subgroup_selection_kb(cluster_name: str, subgroups: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i, title in enumerate(subgroups):
        builder.button(
            text=title,
            callback_data=AdminClusterCallback(action="apply_tariff_subgroup", data=f"{cluster_name}|{i}").pack(),
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К серверам",
            callback_data=AdminClusterCallback(action="set_subgroup", data=cluster_name).pack(),
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def build_tariff_selection_kb(cluster_name: str, tariffs: list, selected: set[int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    grouped: dict[str | None, list] = {}
    for t in tariffs:
        subgroup = t.subgroup_title
        grouped.setdefault(subgroup, []).append(t)

    subgroups_sorted = sorted(grouped.keys(), key=lambda x: (x is None, x or ""))

    for subgroup in subgroups_sorted:
        tariffs_list = grouped[subgroup]

        if subgroup:
            builder.row(
                InlineKeyboardButton(
                    text=f"━━ {subgroup} ━━",
                    callback_data="noop",
                )
            )

        for t in tariffs_list:
            mark = "✅" if t.id in selected else "⬜️"
            builder.row(
                InlineKeyboardButton(
                    text=f"{mark} {t.name}",
                    callback_data=AdminClusterCallback(action="toggle_tariff", data=f"{cluster_name}|{t.id}").pack(),
                )
            )

    builder.row(
        InlineKeyboardButton(
            text="✅ Применить",
            callback_data=AdminClusterCallback(action="apply_tariffs", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ К серверам",
            callback_data=AdminClusterCallback(action="set_subgroup", data=cluster_name).pack(),
        )
    )

    return builder.as_markup()


def build_cluster_management_kb(cluster_name: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(text="📡 Серверы", callback_data=f"cluster_servers|{cluster_name}")
    builder.button(
        text="🌐 Доступность",
        callback_data=AdminClusterCallback(action="availability", data=cluster_name).pack(),
    )
    builder.button(
        text="🔄 Синхронизация",
        callback_data=AdminClusterCallback(action="sync", data=cluster_name).pack(),
    )
    builder.button(
        text="💾 Бэкап",
        callback_data=AdminClusterCallback(action="backup", data=cluster_name).pack(),
    )
    builder.button(
        text="⏳ Добавить время",
        callback_data=AdminClusterCallback(action="add_time", data=cluster_name).pack(),
    )
    builder.button(
        text="💸 Тариф",
        callback_data=AdminClusterCallback(action="set_tariff", data=cluster_name).pack(),
    )
    builder.button(
        text="✏️ Переименовать",
        callback_data=AdminClusterCallback(action="rename", data=cluster_name).pack(),
    )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="clusters").pack()))

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
            text="📍 Синхронизировать",
            callback_data=AdminClusterCallback(action="sync-cluster", data=cluster_name).pack(),
        )
    )

    builder.row(build_admin_back_btn("clusters"))

    return builder.as_markup()


def build_panel_type_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🌐 3X-UI", callback_data=AdminClusterCallback(action="panel_3xui").pack())
    builder.button(
        text="🌀 Remnawave",
        callback_data=AdminClusterCallback(action="panel_remnawave").pack(),
    )
    builder.row(build_admin_back_btn("clusters"))
    return builder.as_markup()


def build_tariff_group_selection_kb(cluster_name: str, groups: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group_id, group_code in groups:
        builder.button(
            text=group_code,
            callback_data=AdminClusterCallback(action="apply_tariff_group", data=f"{cluster_name}|{group_id}").pack(),
        )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack(),
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def build_select_group_servers_kb(cluster_name: str, cluster_servers: list, selected: set[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    names = []
    for s in cluster_servers:
        if isinstance(s, str):
            names.append(s)
        elif isinstance(s, dict):
            names.append(s.get("server_name") or s.get("name") or str(s))
        else:
            names.append(getattr(s, "server_name", None) or getattr(s, "name", None) or str(s))

    for i, name in enumerate(names):
        mark = "✅" if name in selected else "⬜️"
        builder.row(
            InlineKeyboardButton(
                text=f"{mark} {name}",
                callback_data=AdminClusterCallback(action="toggle_server_group", data=f"{cluster_name}|{i}").pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="📚 Выбрать спецгруппу",
            callback_data=AdminClusterCallback(action="choose_group", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="♻️ Сбросить выбор",
            callback_data=AdminClusterCallback(action="reset_group_selection", data=cluster_name).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack(),
        )
    )
    return builder.as_markup()


def build_tariff_group_selection_for_servers_kb(
    cluster_name: str, groups: list[tuple[int, str]]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group_id, group_code in groups:
        builder.button(
            text=group_code,
            callback_data=AdminClusterCallback(
                action="apply_group_to_servers", data=f"{cluster_name}|{group_id}"
            ).pack(),
        )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminClusterCallback(action="set_group", data=cluster_name).pack(),
        )
    )
    builder.adjust(1)
    return builder.as_markup()


def build_availability_kb(cluster_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Обновить",
                    callback_data=AdminClusterCallback(action="availability", data=cluster_name).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=BACK, callback_data=AdminClusterCallback(action="manage", data=cluster_name).pack()
                )
            ],
        ]
    )
