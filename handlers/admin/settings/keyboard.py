from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.settings.money_config import get_currency_mode
from settings.buttons import BACK

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


REMNAWAVE_HOSTS_PER_PAGE = 6
from .settings_config import (
    BUTTON_TITLES,
    MODES_TITLES,
    MONEY_FIELDS,
    NOTIFICATION_TIME_FIELDS,
    NOTIFICATION_TITLES,
    PAYMENT_PROVIDER_TITLES,
)


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


def build_settings_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="Кассы",
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

    builder.adjust(2, 2, 2, 2, 1)
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
    order_button = InlineKeyboardButton(
        text="📋 Порядок касс",
        callback_data=AdminPanelCallback(action="settings_providers_order").pack(),
    )
    return build_toggle_section_keyboard(
        titles=PAYMENT_PROVIDER_TITLES,
        state=providers_state,
        action="settings_cashbox_toggle",
        columns=2,
        back_action="settings",
        extra_rows=[[order_button]],
    )


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

    return build_toggle_section_keyboard(
        titles=NOTIFICATION_TITLES,
        state={k: bool(notifications_state.get(k, False)) for k in NOTIFICATION_TITLES},
        action="settings_notification_toggle",
        columns=1,
        back_action="settings",
        extra_rows=[[intervals_button]],
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
