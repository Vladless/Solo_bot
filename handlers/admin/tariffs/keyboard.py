from collections import defaultdict

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.tariffs import create_subgroup_hash
from settings.buttons import BACK

from ..panel.keyboard import AdminPanelCallback


class AdminTariffCallback(CallbackData, prefix="tariff"):
    action: str


def _short(name: str | None, limit: int = 14) -> str:
    """Возвращает подпись кнопки, обрезанную до limit символов."""
    value = (name or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def build_tariff_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🆕 Новый тариф", callback_data=AdminTariffCallback(action="create").pack())
    builder.button(text="📋 Мои тарифы", callback_data=AdminTariffCallback(action="list").pack())
    builder.button(text="🔢 Расположение", callback_data=AdminTariffCallback(action="arrange").pack())
    builder.adjust(2)
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
    from database.tariffs import create_subgroup_hash
    from handlers.keys.utils import order_tariff_items

    builder = InlineKeyboardBuilder()

    grouped_tariffs = defaultdict(list)
    for t in tariffs:
        grouped_tariffs[t.get("subgroup_title")].append(t)

    for subgroup in grouped_tariffs:
        grouped_tariffs[subgroup].sort(key=lambda x: x.get("sort_order") or 0)

    def _tariff_row(t: dict) -> None:
        builder.row(
            InlineKeyboardButton(
                text="⬆️",
                callback_data=AdminTariffCallback(action=f"quick_move_up|{t.get('id')}|{group_code}").pack(),
            ),
            InlineKeyboardButton(
                text=_short(t.get("name")),
                callback_data=AdminTariffCallback(action=f"view|{t.get('id')}").pack(),
            ),
            InlineKeyboardButton(
                text="⬇️",
                callback_data=AdminTariffCallback(action=f"quick_move_down|{t.get('id')}|{group_code}").pack(),
            ),
        )

    for kind, payload in order_tariff_items(grouped_tariffs):
        if kind == "tariff":
            _tariff_row(payload)
        else:
            subgroup_hash = create_subgroup_hash(payload, group_code)
            builder.row(
                InlineKeyboardButton(text="⬆️", callback_data=f"submove_up|{subgroup_hash}|{group_code}"),
                InlineKeyboardButton(
                    text=f"📁 {_short(payload)}", callback_data=AdminTariffCallback(action="arrange").pack()
                ),
                InlineKeyboardButton(text="⬇️", callback_data=f"submove_down|{subgroup_hash}|{group_code}"),
            )
            for t in grouped_tariffs[payload]:
                _tariff_row(t)

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
        subgroup = t.get("subgroup_title") or None
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
        title = f"#{t['id']} · {_short(t['name'])} · {t['price_rub']}₽"
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

    builder.row(InlineKeyboardButton(text="Объединить в подгруппу", callback_data=f"start_subgrouping|{group_code}"))

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
                    text="🔧 Конфигуратор",
                    callback_data=f"edit_config|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📑 Дублировать тариф",
                    callback_data=AdminTariffCallback(action=f"duplicate|{tariff_id}").pack(),
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


def build_tariff_visibility_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👁 Показывать всем", callback_data=f"tvisset|{tariff_id}|all|none")],
            [
                InlineKeyboardButton(
                    text="✅ С активной подпиской", callback_data=f"tvisset|{tariff_id}|only|has_active"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Без активной подписки", callback_data=f"tvisset|{tariff_id}|except|has_active"
                )
            ],
            [InlineKeyboardButton(text="✅ Только: горячий лид", callback_data=f"tvisset|{tariff_id}|only|hot_lead")],
            [InlineKeyboardButton(text="🚫 Кроме: горячий лид", callback_data=f"tvisset|{tariff_id}|except|hot_lead")],
            [
                InlineKeyboardButton(
                    text="✅ Только: активных ≥ N", callback_data=f"tvisset|{tariff_id}|only|active_count"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Кроме: активных ≥ N", callback_data=f"tvisset|{tariff_id}|except|active_count"
                )
            ],
            [InlineKeyboardButton(text=BACK, callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack())],
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
            [
                InlineKeyboardButton(
                    text="⏳ Задержка покупки",
                    callback_data=f"edit_field|{tariff_id}|cooldown_days",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Описание",
                    callback_data=f"edit_field|{tariff_id}|description",
                )
            ],
            [InlineKeyboardButton(text="👁 Видимость", callback_data=f"tvis|{tariff_id}")],
            [InlineKeyboardButton(text="🔘 Активность", callback_data=f"toggle_active|{tariff_id}")],
            [InlineKeyboardButton(text=BACK, callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack())],
        ]
    )
