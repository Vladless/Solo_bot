from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
            text="⬅️ Назад", callback_data=AdminPanelCallback(action="admin").pack()
        )
    )
    return builder.as_markup()


def build_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить", callback_data="cancel_tariff_creation"
                )
            ]
        ]
    )


def build_tariff_groups_kb(groups: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(
            text=group,
            callback_data=AdminTariffCallback(action=f"group|{group}").pack(),
        )
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад", callback_data=AdminPanelCallback(action="tariffs").pack()
        )
    )
    return builder.as_markup()


def build_tariff_list_kb(tariffs: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if tariffs:
        group_code = tariffs[0]["group_code"]
    else:
        group_code = "unknown"

    for t in tariffs:
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

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад", callback_data=AdminTariffCallback(action="list").pack()
        )
    )

    return builder.as_markup()


def build_single_tariff_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=AdminTariffCallback(
                        action=f"edit|{tariff_id}"
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=AdminTariffCallback(
                        action=f"delete|{tariff_id}"
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=AdminTariffCallback(action="list").pack(),
                )
            ],
        ]
    )


def build_edit_tariff_fields_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Название", callback_data=f"edit_field|{tariff_id}|name"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Длительность",
                    callback_data=f"edit_field|{tariff_id}|duration_days",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Цена", callback_data=f"edit_field|{tariff_id}|price_rub"
                )
            ],
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
                    text="🔘 Активность", callback_data=f"toggle_active|{tariff_id}"
                )
            ],
            [InlineKeyboardButton(
                text="⬅️ Назад", 
                callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack()
            )]
        ]
    )
