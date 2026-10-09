from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings.money_config import get_currency_mode
from settings.buttons import ADMIN_ACCESS_SETTINGS, BACK, PAYMENT_CASHBOXES, PAYMENT_CASHBOX_ENABLED

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn
from .settings_config import (
    ADMIN_NOTIFICATION_TITLES,
    BUTTON_TITLES,
    MODES_TITLES,
    MONEY_FIELDS,
    NOTIFICATION_TIME_FIELDS,
    NOTIFICATION_TITLES,
    PAYMENT_CASHBOX_GROUPS,
    PAYMENT_CASHBOX_TITLES,
    PAYMENT_PROVIDER_TITLES,
)


REMNAWAVE_HOSTS_PER_PAGE = 6
LOAD_MONITOR_SNAPSHOT_NODES_PER_PAGE = 4


def build_toggle_section_keyboard(
    titles: dict[str, str],
    state: dict[str, bool],
    action: str,
    columns: int,
    back_action: str = "settings",
    extra_rows: list[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for index, key in enumerate(titles.keys(), start=1):
        title = titles[key]
        current_state = bool(state.get(key, False))
        prefix = "✅" if current_state else "❌"
        builder.button(
            text=f"{prefix} {title}",
            callback_data=AdminPanelCallback(
                action=action,
                page=index,
            ).pack(),
        )

    builder.adjust(columns)

    if extra_rows:
        for row in extra_rows:
            builder.row(*row)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action=back_action).pack(),
        )
    )

    return builder.as_markup()


def build_settings_kb(*, show_access: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=PAYMENT_CASHBOXES,
        callback_data=AdminPanelCallback(action="settings_cashboxes").pack(),
    )
    builder.button(
        text="Деньги",
        callback_data=AdminPanelCallback(action="settings_money").pack(),
    )
    builder.button(
        text="Кнопки",
        callback_data=AdminPanelCallback(action="settings_buttons").pack(),
    )
    builder.button(
        text="Уведомления",
        callback_data=AdminPanelCallback(action="settings_notifications").pack(),
    )
    builder.button(
        text="Режимы",
        callback_data=AdminPanelCallback(action="settings_modes").pack(),
    )
    builder.button(
        text="Тарификация",
        callback_data=AdminPanelCallback(action="settings_tariffs").pack(),
    )
    builder.button(
        text="📄 Документы",
        callback_data=AdminPanelCallback(action="settings_legal").pack(),
    )
    builder.button(
        text="🌐 Сайт",
        callback_data=AdminPanelCallback(action="settings_web").pack(),
    )
    builder.button(
        text="🌀 Remnawave",
        callback_data=AdminPanelCallback(action="settings_remnawave").pack(),
    )
    if show_access:
        builder.button(
            text=ADMIN_ACCESS_SETTINGS,
            callback_data=AdminPanelCallback(action="settings_access").pack(),
        )

    builder.adjust(2)
    builder.row(build_admin_back_btn())

    return builder.as_markup()


def build_settings_buttons_kb(buttons_state: dict[str, bool]) -> InlineKeyboardMarkup:
    layout_button = InlineKeyboardButton(
        text="📋 Порядок кнопок",
        callback_data=AdminPanelCallback(action="settings_menu_layout").pack(),
    )
    return build_toggle_section_keyboard(
        titles=BUTTON_TITLES,
        state=buttons_state,
        action="settings_button_toggle",
        columns=2,
        back_action="settings",
        extra_rows=[[layout_button]],
    )


def build_settings_cashboxes_kb(providers_state: dict[str, bool]) -> InlineKeyboardMarkup:
    """Собирает список касс с переходом к настройкам."""
    builder = InlineKeyboardBuilder()
    for index, (cashbox, providers) in enumerate(PAYMENT_CASHBOX_GROUPS.items(), start=1):
        enabled = any(providers_state.get(provider, False) for provider in providers)
        builder.button(
            text=f"{'✅' if enabled else '❌'} {PAYMENT_CASHBOX_TITLES[cashbox]}",
            callback_data=AdminPanelCallback(action="settings_cashbox", page=index).pack(),
        )
    builder.adjust(2)
    order_button = InlineKeyboardButton(
        text="📋 Порядок касс",
        callback_data=AdminPanelCallback(action="settings_providers_order").pack(),
    )
    builder.row(order_button)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings").pack()))
    return builder.as_markup()


def build_settings_cashbox_kb(cashbox: str, providers_state: dict[str, bool]) -> InlineKeyboardMarkup:
    """Собирает способы оплаты выбранной кассы."""
    providers = PAYMENT_CASHBOX_GROUPS[cashbox]
    keys = list(PAYMENT_PROVIDER_TITLES)
    builder = InlineKeyboardBuilder()
    for provider in providers:
        enabled = bool(providers_state.get(provider, False))
        title = PAYMENT_PROVIDER_TITLES[provider] if len(providers) > 1 else PAYMENT_CASHBOX_ENABLED
        builder.row(
            InlineKeyboardButton(
                text=f"{'✅' if enabled else '❌'} {title}",
                callback_data=AdminPanelCallback(
                    action="settings_cashbox_toggle", page=keys.index(provider) + 1
                ).pack(),
            )
        )
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_cashboxes").pack()))
    return builder.as_markup()


def build_providers_order_kb(sorted_names: list[str]) -> InlineKeyboardMarkup:
    """Клавиатура для управления порядком отображения касс."""
    builder = InlineKeyboardBuilder()

    for idx, name in enumerate(sorted_names):
        title = PAYMENT_PROVIDER_TITLES.get(name, name)
        pos = idx + 1
        builder.row(
            InlineKeyboardButton(
                text="⬆️",
                callback_data=AdminPanelCallback(
                    action="settings_order_up",
                    page=pos,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"{pos}. {title[:15]}",
                callback_data=AdminPanelCallback(
                    action="settings_providers_order",
                ).pack(),
            ),
            InlineKeyboardButton(
                text="⬇️",
                callback_data=AdminPanelCallback(
                    action="settings_order_down",
                    page=pos,
                ).pack(),
            ),
        )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Сбросить порядок",
            callback_data=AdminPanelCallback(action="settings_order_reset").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings_cashboxes").pack(),
        )
    )

    return builder.as_markup()


def build_settings_notifications_kb(notifications_state: dict[str, object]) -> InlineKeyboardMarkup:
    intervals_button = InlineKeyboardButton(
        text="Интервалы",
        callback_data=AdminPanelCallback(action="settings_notifications_intervals").pack(),
    )
    admin_button = InlineKeyboardButton(
        text="Админу",
        callback_data=AdminPanelCallback(action="settings_notifications_admin").pack(),
    )

    return build_toggle_section_keyboard(
        titles=NOTIFICATION_TITLES,
        state={k: bool(notifications_state.get(k, False)) for k in NOTIFICATION_TITLES},
        action="settings_notification_toggle",
        columns=1,
        back_action="settings",
        extra_rows=[[intervals_button, admin_button]],
    )


def build_settings_notifications_admin_kb(notifications_state: dict[str, object]) -> InlineKeyboardMarkup:
    return build_toggle_section_keyboard(
        titles=ADMIN_NOTIFICATION_TITLES,
        state={k: bool(notifications_state.get(k, False)) for k in ADMIN_NOTIFICATION_TITLES},
        action="settings_notification_admin_toggle",
        columns=1,
        back_action="settings_notifications",
    )


def build_settings_notifications_intervals_kb(notifications_state: dict[str, object]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    keys = list(NOTIFICATION_TIME_FIELDS.keys())
    for index, key in enumerate(keys, start=1):
        title = NOTIFICATION_TIME_FIELDS[key]
        value = notifications_state.get(key)
        value_text = "не задано" if value is None else str(value)

        builder.button(
            text=f"{title}: {value_text}",
            callback_data=AdminPanelCallback(
                action="settings_notification_interval_edit",
                page=index,
            ).pack(),
        )

    builder.adjust(1)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings_notifications").pack(),
        )
    )

    return builder.as_markup()


def build_settings_modes_kb(modes_state: dict[str, bool]) -> InlineKeyboardMarkup:
    return build_toggle_section_keyboard(
        titles=MODES_TITLES,
        state=modes_state,
        action="settings_modes_toggle",
        columns=2,
        back_action="settings",
    )


def build_settings_money_kb(money_state: dict[str, object]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    field_keys = list(MONEY_FIELDS.keys())
    for index, key in enumerate(field_keys, start=1):
        title = MONEY_FIELDS[key]
        value = money_state.get(key)

        if key == "RUB_TO_USD":
            if value is False or value is None:
                value_text = "по ЦБ РФ"
            else:
                value_text = str(value)
        elif key == "CASHBACK":
            try:
                numeric_value = float(value) if value not in (None, False) else 0.0
            except (TypeError, ValueError):
                numeric_value = 0.0
            if numeric_value <= 0:
                value_text = "выкл"
            else:
                value_text = f"{numeric_value:g} %"
        else:
            value_text = "не задано" if value is None else str(value)

        builder.button(
            text=f"{title}: {value_text}",
            callback_data=AdminPanelCallback(
                action="settings_money_edit",
                page=index,
            ).pack(),
        )

    mode, one_screen = get_currency_mode()
    if mode == "RUB+USD" and one_screen:
        mode_text = "RUB+USD (одним экраном)"
    else:
        mode_text = mode

    builder.button(
        text=f"Режим валют: {mode_text}",
        callback_data=AdminPanelCallback(
            action="settings_money_currency",
            page=0,
        ).pack(),
    )

    builder.adjust(1)

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings").pack(),
        )
    )

    return builder.as_markup()


def build_settings_remnawave_kb(
    node_enabled: bool,
    rotation_enabled: bool,
    load_monitor_enabled: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅' if node_enabled else '❌'} Проверка нод",
            callback_data=AdminPanelCallback(action="rw_node_menu").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅' if rotation_enabled else '❌'} Ротация хостов",
            callback_data=AdminPanelCallback(action="rw_rot_menu").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{'✅' if load_monitor_enabled else '❌'} Мониторинг нагрузки",
            callback_data=AdminPanelCallback(action="rw_load_menu").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="settings").pack(),
        )
    )
    return builder.as_markup()


def build_settings_remnawave_node_kb(
    node_enabled: bool, interval_min: int, auto_disable_enabled: bool = False
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Проверка вкл" if node_enabled else "❌ Проверка выкл",
        callback_data=AdminPanelCallback(action="rw_node_toggle").pack(),
    )
    builder.button(
        text=f"⏱ Интервал: {interval_min} мин",
        callback_data=AdminPanelCallback(action="rw_node_interval").pack(),
    )
    builder.button(
        text=f"{'✅' if auto_disable_enabled else '❌'} Авто-отключение",
        callback_data=AdminPanelCallback(action="rw_autodisable_toggle").pack(),
    )
    if auto_disable_enabled:
        builder.button(
            text="🔌 Синхронизировать",
            callback_data=AdminPanelCallback(action="rw_node_sync_now").pack(),
        )
    builder.button(text="🖧 Выбрать ноды", callback_data=AdminPanelCallback(action="rw_node_sel").pack())
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_remnawave").pack()))
    return builder.as_markup()


def build_settings_remnawave_health_nodes_kb(
    page: int,
    nodes: list[tuple[str, dict[str, Any]]],
    allowed: set[str],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(nodes) + REMNAWAVE_HOSTS_PER_PAGE - 1) // REMNAWAVE_HOSTS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    chunk = nodes[start : start + REMNAWAVE_HOSTS_PER_PAGE]

    for idx, (_, node) in enumerate(chunk):
        node_uuid = str(node.get("uuid"))
        marker = "✅" if node_uuid in allowed else "▫️"
        name = (node.get("name") or node.get("address") or node_uuid)[:30]
        global_idx = start + idx
        builder.row(
            InlineKeyboardButton(
                text=f"{marker} {name}",
                callback_data=AdminPanelCallback(action="rw_node_sel_toggle", page=global_idx).pack(),
            )
        )

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=AdminPanelCallback(action="rw_node_sel", page=page - 1).pack(),
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=AdminPanelCallback(action="rw_node_sel", page=page).pack(),
            )
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=AdminPanelCallback(action="rw_node_sel", page=page + 1).pack(),
                )
            )
        builder.row(*nav_row)

    builder.row(
        InlineKeyboardButton(
            text="✅ Все на странице",
            callback_data=AdminPanelCallback(action="rw_node_sel_all", page=page).pack(),
        ),
        InlineKeyboardButton(
            text="▫️ Снять все",
            callback_data=AdminPanelCallback(action="rw_node_sel_clear", page=page).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="rw_node_menu").pack(),
        )
    )
    return builder.as_markup()


def build_settings_remnawave_rotation_kb(rotation_enabled: bool, interval_min: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Ротация вкл" if rotation_enabled else "❌ Ротация выкл",
        callback_data=AdminPanelCallback(action="rw_rot_toggle").pack(),
    )
    builder.button(
        text=f"⏱ Интервал: {interval_min} мин",
        callback_data=AdminPanelCallback(action="rw_rot_interval").pack(),
    )
    builder.button(text="📋 Выбрать хосты", callback_data=AdminPanelCallback(action="rw_rot_hosts", page=1).pack())
    builder.button(text="🔀 Перемешать", callback_data=AdminPanelCallback(action="rw_rot_run_now").pack())
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_remnawave").pack()))
    return builder.as_markup()


def build_settings_remnawave_load_kb(
    enabled: bool, interval_min: int, overload_confirmations: int = 2
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Мониторинг вкл" if enabled else "❌ Мониторинг выкл",
            callback_data=AdminPanelCallback(action="rw_load_toggle").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"⏱ Интервал: {interval_min} мин",
            callback_data=AdminPanelCallback(action="rw_load_interval").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f"🔁 Подтверждений перегрузки: {overload_confirmations}",
            callback_data=AdminPanelCallback(action="rw_load_confirmations").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗂 Группы серверов", callback_data=AdminPanelCallback(action="rw_load_groups").pack()
        ),
        InlineKeyboardButton(text="📊 Срез сейчас", callback_data=AdminPanelCallback(action="rw_load_snapshot").pack()),
    )
    builder.row(
        InlineKeyboardButton(
            text="↩️ Вернуть снятые теги", callback_data=AdminPanelCallback(action="rw_load_restore").pack()
        )
    )
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="settings_remnawave").pack()))
    return builder.as_markup()


def build_remnawave_load_snapshot_kb(
    groups: list[dict[str, Any]], group_index: int, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    group_index = max(0, min(group_index, len(groups) - 1)) if groups else 0
    page = max(1, min(page, total_pages))
    group_action = f"rw_load_snapshot_g{group_index}"

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=AdminPanelCallback(action=group_action, page=page - 1).pack(),
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=AdminPanelCallback(action=group_action, page=page).pack(),
            )
        )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=AdminPanelCallback(action=group_action, page=page + 1).pack(),
                )
            )
        builder.row(*nav)

    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=AdminPanelCallback(action=group_action, page=page).pack(),
        ),
        InlineKeyboardButton(
            text="↩️ К мониторингу",
            callback_data=AdminPanelCallback(action="rw_load_menu").pack(),
        ),
    )

    for index in range(0, len(groups), 2):
        row = []
        for group_index_in_row in range(index, min(index + 2, len(groups))):
            group = groups[group_index_in_row]
            marker = "• " if group_index_in_row == group_index else ""
            name = str(group.get("name") or "Без названия")[:30]
            row.append(
                InlineKeyboardButton(
                    text=f"{marker}{name}",
                    callback_data=AdminPanelCallback(action=f"rw_load_snapshot_g{group_index_in_row}", page=1).pack(),
                )
            )
        builder.row(*row)

    return builder.as_markup()


def build_remnawave_load_groups_kb(groups: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, group in enumerate(groups):
        name = str(group.get("name") or "Без названия")[:24]
        selected = len(group.get("node_keys") or [])
        limit = int(group.get("max_cpu_percent") or 0)
        limit_text = f"≤{limit}% load/ядро" if limit else "наблюдение"
        builder.row(
            InlineKeyboardButton(
                text=f"🗂 {name} · {selected} нод · {limit_text}",
                callback_data=AdminPanelCallback(action="rw_load_group", page=index + 1).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="➕ Новая группа", callback_data=AdminPanelCallback(action="rw_load_group_add").pack()
        )
    )
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="rw_load_menu").pack()))
    return builder.as_markup()


def build_remnawave_load_group_kb(group: dict[str, Any], group_index: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"🖧 Выбрать ноды ({len(group.get('node_keys') or [])})",
            callback_data=AdminPanelCallback(action="rw_load_nodes", page=1).pack(),
        )
    )
    limit = int(group.get("max_cpu_percent") or 0)
    builder.row(
        InlineKeyboardButton(
            text=f"🔢 Порог: {limit}% load/ядро" if limit else "🔢 Порог: только наблюдение",
            callback_data=AdminPanelCallback(action="rw_load_group_limit", page=group_index).pack(),
        )
    )
    tag = str(group.get("routing_tag") or "")
    builder.row(
        InlineKeyboardButton(
            text=f"🏷 Тег авто-пула: {tag or 'не задан'}",
            callback_data=AdminPanelCallback(action="rw_load_group_tag", page=group_index).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Удалить группу",
            callback_data=AdminPanelCallback(action="rw_load_group_delete", page=group_index).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="rw_load_groups").pack(),
        )
    )
    return builder.as_markup()


def build_remnawave_load_nodes_kb(
    page: int,
    nodes: list[tuple[str, dict[str, Any]]],
    selected: set[str],
    group_index: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(nodes) + REMNAWAVE_HOSTS_PER_PAGE - 1) // REMNAWAVE_HOSTS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    chunk = nodes[start : start + REMNAWAVE_HOSTS_PER_PAGE]
    for offset, (api_url, node) in enumerate(chunk):
        node_uuid = str(node.get("uuid") or "")
        node_key = f"{api_url.rstrip('/')}::{node_uuid}"
        marker = "✅" if node_key in selected else "▫️"
        name = str(node.get("name") or node.get("address") or node_uuid)
        online = node.get("usersOnline")
        system = node.get("system") or {}
        info = system.get("info") or {}
        stats = system.get("stats") or {}
        load_avg = stats.get("loadAvg") or []
        cores = info.get("cpus")
        if isinstance(load_avg, list) and load_avg and cores:
            try:
                online_text = f" · load {float(load_avg[0]) / float(cores) * 100:.0f}%"
            except (TypeError, ValueError, ZeroDivisionError):
                online_text = " · без метрик"
        else:
            online_text = " · без метрик"
        if online is not None:
            online_text += f" · {online} онлайн"
        if not node.get("isConnected"):
            online_text += " · офлайн"
        builder.row(
            InlineKeyboardButton(
                text=f"{marker} {name[:28]}{online_text}",
                callback_data=AdminPanelCallback(action="rw_load_node_toggle", page=start + offset).pack(),
            )
        )
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 1:
            nav.append(
                InlineKeyboardButton(
                    text="⬅️", callback_data=AdminPanelCallback(action="rw_load_nodes", page=page - 1).pack()
                )
            )
        nav.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}", callback_data=AdminPanelCallback(action="rw_load_nodes", page=page).pack()
            )
        )
        if page < total_pages:
            nav.append(
                InlineKeyboardButton(
                    text="➡️", callback_data=AdminPanelCallback(action="rw_load_nodes", page=page + 1).pack()
                )
            )
        builder.row(*nav)
    builder.row(
        InlineKeyboardButton(
            text="✅ Все на странице", callback_data=AdminPanelCallback(action="rw_load_nodes_all", page=page).pack()
        ),
        InlineKeyboardButton(
            text="▫️ Снять на странице", callback_data=AdminPanelCallback(action="rw_load_nodes_clear", page=page).pack()
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK, callback_data=AdminPanelCallback(action="rw_load_group", page=group_index).pack()
        )
    )
    return builder.as_markup()


def build_settings_remnawave_hosts_kb(
    page: int,
    hosts: list[tuple[str, dict[str, Any]]],
    allowed: set[str],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    total_pages = max(1, (len(hosts) + REMNAWAVE_HOSTS_PER_PAGE - 1) // REMNAWAVE_HOSTS_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * REMNAWAVE_HOSTS_PER_PAGE
    chunk = hosts[start : start + REMNAWAVE_HOSTS_PER_PAGE]

    for idx, (_, host) in enumerate(chunk):
        host_uuid = str(host.get("uuid"))
        marker = "✅" if host_uuid in allowed else "▫️"
        remark = (host.get("remark") or host.get("address") or host_uuid)[:30]
        global_idx = start + idx
        builder.row(
            InlineKeyboardButton(
                text=f"{marker} {remark}",
                callback_data=AdminPanelCallback(action="rw_rot_toggle_host", page=global_idx).pack(),
            )
        )

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=AdminPanelCallback(action="rw_rot_hosts", page=page - 1).pack(),
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=AdminPanelCallback(action="rw_rot_hosts", page=page).pack(),
            )
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=AdminPanelCallback(action="rw_rot_hosts", page=page + 1).pack(),
                )
            )
        builder.row(*nav_row)

    builder.row(
        InlineKeyboardButton(
            text="✅ Все на странице",
            callback_data=AdminPanelCallback(action="rw_rot_select_all", page=page).pack(),
        ),
        InlineKeyboardButton(
            text="▫️ Сбросить страницу",
            callback_data=AdminPanelCallback(action="rw_rot_clear_page", page=page).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="rw_rot_menu").pack(),
        )
    )
    return builder.as_markup()
