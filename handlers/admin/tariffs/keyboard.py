from collections import defaultdict

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from handlers.buttons import BACK

from database.tariffs import create_subgroup_hash

from ..panel.keyboard import AdminPanelCallback


class AdminTariffCallback(CallbackData, prefix="tariff"):
    action: str


def build_tariff_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🆕 Новый тариф",
            callback_data=AdminTariffCallback(action="create").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📋 Мои тарифы",
            callback_data=AdminTariffCallback(action="list").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🔢 Расположение тарифов",
            callback_data=AdminTariffCallback(action="arrange").pack(),
        )
    )
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="admin").pack()))
    return builder.as_markup()


def build_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_tariff_creation")]]
    )


def build_tariff_arrangement_groups_kb(groups: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = []

    for i, group in enumerate(groups):
        row.append(
            InlineKeyboardButton(
                text=group,
                callback_data=AdminTariffCallback(action=f"arrange_group|{group}").pack(),
            )
        )
        if len(row) == 2 or i == len(groups) - 1:
            builder.row(*row)
            row = []

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminTariffCallback(action="list").pack(),
        )
    )
    return builder.as_markup()


def build_tariffs_arrangement_kb(group_code: str, tariffs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    grouped_tariffs = defaultdict(list)
    for t in tariffs:
        grouped_tariffs[t.get("subgroup_title")].append(t)

    for subgroup in grouped_tariffs:
        grouped_tariffs[subgroup].sort(key=lambda x: x.get("sort_order"))

    if grouped_tariffs.get(None):
        for t in grouped_tariffs[None]:
            builder.row(
                InlineKeyboardButton(
                    text="⬆️",
                    callback_data=AdminTariffCallback(action=f"quick_move_up|{t.get('id')}|{group_code}").pack(),
                ),
                InlineKeyboardButton(
                    text=f"  {t.get('name')}  ", callback_data=AdminTariffCallback(action=f"view|{t.get('id')}").pack()
                ),
                InlineKeyboardButton(
                    text="⬇️",
                    callback_data=AdminTariffCallback(action=f"quick_move_down|{t.get('id')}|{group_code}").pack(),
                ),
            )

    for subgroup, tariffs_list in grouped_tariffs.items():
        if subgroup:
            builder.row(
                InlineKeyboardButton(text=f"📁 {subgroup}", callback_data=AdminTariffCallback(action="arrange").pack())
            )
            for t in tariffs_list:
                builder.row(
                    InlineKeyboardButton(
                        text="⬆️",
                        callback_data=AdminTariffCallback(action=f"quick_move_up|{t.get('id')}|{group_code}").pack(),
                    ),
                    InlineKeyboardButton(
                        text=f"  {t.get('name')}  ",
                        callback_data=AdminTariffCallback(action=f"view|{t.get('id')}").pack(),
                    ),
                    InlineKeyboardButton(
                        text="⬇️",
                        callback_data=AdminTariffCallback(action=f"quick_move_down|{t.get('id')}|{group_code}").pack(),
                    ),
                )

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="tariffs").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data=AdminPanelCallback(action="admin").pack(),
        )
    )

    return builder.as_markup()


def build_tariff_groups_kb(groups: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = []

    for i, group in enumerate(groups):
        row.append(
            InlineKeyboardButton(
                text=group,
                callback_data=AdminTariffCallback(action=f"group|{group}").pack(),
            )
        )
        if len(row) == 2 or i == len(groups) - 1:
            builder.row(*row)
            row = []
    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminPanelCallback(action="tariffs").pack(),
        )
    )
    return builder.as_markup()


def build_tariff_list_kb(tariffs: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if tariffs:
        group_code = tariffs[0]["group_code"]
    else:
        group_code = "unknown"

    grouped = defaultdict(list)
    for t in tariffs:
        subgroup = t.get("subgroup_title")
        grouped[subgroup].append(t)

    sorted_subgroups = sorted(
        [k for k in grouped if k], key=lambda x: (sum(t.get("sort_order", 1) for t in grouped[x]), x)
    )

    for subgroup_title in sorted_subgroups:
        subgroup_hash = create_subgroup_hash(subgroup_title, group_code)
        builder.row(
            InlineKeyboardButton(text=f"{subgroup_title}", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}")
        )

    for t in grouped.get(None, []):
        title = f"{t['name']} — {t['price_rub']}₽"
        builder.row(
            InlineKeyboardButton(
                text=title,
                callback_data=AdminTariffCallback(action=f"view|{t['id']}").pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="➕ Добавить тариф",
            callback_data=AdminTariffCallback(action=f"create|{group_code}").pack(),
        )
    )

    builder.row(InlineKeyboardButton(text="Сгруппировать в подгруппу", callback_data=f"start_subgrouping|{group_code}"))

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminTariffCallback(action="list").pack(),
        )
    )

    return builder.as_markup()


def build_single_tariff_kb(
    tariff_id: int,
    group_code: str | None = None,
    configurable: bool | None = None,
) -> InlineKeyboardMarkup:
    configurator_title = "⚙️ Конфигуратор"
    if configurable is True:
        configurator_title += " ✅"
    elif configurable is False:
        configurator_title += " ❌"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=configurator_title,
                    callback_data=f"toggle_configurable|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔧 Настройки конфигуратора",
                    callback_data=f"edit_config|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=AdminTariffCallback(action=f"edit|{tariff_id}").pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=AdminTariffCallback(action=f"delete|{tariff_id}").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬆️ Выше",
                    callback_data=AdminTariffCallback(action=f"move_up|{tariff_id}").pack(),
                ),
                InlineKeyboardButton(
                    text="⬇️ Ниже",
                    callback_data=AdminTariffCallback(action=f"move_down|{tariff_id}").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=BACK,
                    callback_data=AdminTariffCallback(action=f"group|{group_code}").pack()
                    if group_code
                    else AdminTariffCallback(action="list").pack(),
                )
            ],
        ]
    )


def build_edit_tariff_fields_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Название", callback_data=f"edit_field|{tariff_id}|name")],
            [
                InlineKeyboardButton(
                    text="📅 Длительность",
                    callback_data=f"edit_field|{tariff_id}|duration_days",
                )
            ],
            [InlineKeyboardButton(text="💰 Цена", callback_data=f"edit_field|{tariff_id}|price_rub")],
            [
                InlineKeyboardButton(
                    text="📦 Трафик (ГБ или 0)",
                    callback_data=f"edit_field|{tariff_id}|traffic_limit",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📱 Лимит устройств",
                    callback_data=f"edit_field|{tariff_id}|device_limit",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔗 VLESS",
                    callback_data=f"edit_field|{tariff_id}|vless",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Внешний сквад",
                    callback_data=f"edit_field|{tariff_id}|external_squad",
                )
            ],
            [InlineKeyboardButton(text="🔘 Активность", callback_data=f"toggle_active|{tariff_id}")],
            [InlineKeyboardButton(text=BACK, callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack())],
        ]
    )
