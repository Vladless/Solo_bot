from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


class BulkCallback(CallbackData, prefix="abulk"):
    step: str
    value: str = ""


ACTIONS = [
    ("days", "⏳ +Время (дни)"),
    ("gb", "📦 +Трафик (ГБ)"),
    ("freeze", "❄️ Заморозить"),
    ("unfreeze", "☀️ Разморозить"),
    ("reissue", "🔁 Перевыпустить"),
    ("reissue_link", "🔗 Перевыпустить со сменой ссылки"),
    ("delete", "❌ Удалить"),
]

FILTERS = [
    ("tariff", "📦 По тарифу"),
    ("created", "📅 По дате создания"),
    ("expiry", "⏰ По сроку истечения"),
    ("cluster", "🌐 По кластеру"),
]


def build_actions_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value, label in ACTIONS:
        builder.row(InlineKeyboardButton(text=label, callback_data=BulkCallback(step="action", value=value).pack()))
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def build_filters_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value, label in FILTERS:
        builder.row(InlineKeyboardButton(text=label, callback_data=BulkCallback(step="filter", value=value).pack()))
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=BulkCallback(step="back_actions").pack()))
    return builder.as_markup()


def build_tariff_groups_kb(groups: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in groups:
        builder.row(
            InlineKeyboardButton(text=f"📁 {code}", callback_data=BulkCallback(step="tgroup", value=code).pack())
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=BulkCallback(step="back_filters").pack()))
    return builder.as_markup()


def build_tariffs_kb(tariffs: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for t in tariffs:
        name = t.get("name") or f"тариф #{t.get('id')}"
        builder.row(
            InlineKeyboardButton(
                text=f"{name} (#{t.get('id')})",
                callback_data=BulkCallback(step="tariff", value=str(t.get("id"))).pack(),
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=BulkCallback(step="back_tgroups").pack()))
    return builder.as_markup()


def build_clusters_kb(clusters: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for name in clusters:
        builder.row(
            InlineKeyboardButton(text=f"🌐 {name}", callback_data=BulkCallback(step="cluster", value=name).pack())
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=BulkCallback(step="back_filters").pack()))
    return builder.as_markup()


def build_created_dir_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Старше N дней", callback_data=BulkCallback(step="created", value="older").pack())
    )
    builder.row(
        InlineKeyboardButton(text="Моложе N дней", callback_data=BulkCallback(step="created", value="newer").pack())
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=BulkCallback(step="back_filters").pack()))
    return builder.as_markup()


def build_expiry_kind_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Уже истекли", callback_data=BulkCallback(step="expiry", value="expired").pack()),
        InlineKeyboardButton(text="Ещё активны", callback_data=BulkCallback(step="expiry", value="active").pack()),
    )
    builder.row(
        InlineKeyboardButton(text="Истекают скоро", callback_data=BulkCallback(step="expiry", value="soon").pack())
    )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data=BulkCallback(step="back_filters").pack()))
    return builder.as_markup()


def build_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=BulkCallback(step="confirm", value="go").pack()),
        InlineKeyboardButton(text="❌ Отмена", callback_data=AdminPanelCallback(action="admin").pack()),
    )
    return builder.as_markup()


def build_done_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ В админку", callback_data=AdminPanelCallback(action="admin").pack()))
    return builder.as_markup()
