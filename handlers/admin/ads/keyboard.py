from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import build_admin_back_btn


class AdminAdsCallback(CallbackData, prefix="admin_ads"):
    action: str
    code: str | None = None


def build_ads_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Новая ссылка", callback_data=AdminAdsCallback(action="create").pack())
    builder.button(text="📊 Список", callback_data=AdminAdsCallback(action="list").pack())
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_ads_list_kb(ads: list, current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    items_per_page = 6

    start = (current_page - 1) * items_per_page
    end = start + items_per_page
    page_ads = ads[start:end]

    row = []
    for i, ad in enumerate(page_ads, 1):
        row.append(
            InlineKeyboardButton(
                text=f"📎 {ad['name']}",
                callback_data=AdminAdsCallback(action="view", code=ad["code"]).pack(),
            )
        )
        if i % 2 == 0 or i == len(page_ads):
            builder.row(*row)
            row = []

    pagination_buttons = []
    if current_page > 1:
        pagination_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=AdminAdsCallback(action="list", code=f"{current_page - 1}").pack(),
            )
        )
    if current_page < total_pages:
        pagination_buttons.append(
            InlineKeyboardButton(
                text="Вперед ➡️",
                callback_data=AdminAdsCallback(action="list", code=f"{current_page + 1}").pack(),
            )
        )
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.row(build_admin_back_btn("ads"))
    return builder.as_markup()


def build_ads_stats_kb(code: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔄 Обновить",
        callback_data=AdminAdsCallback(action="view", code=code).pack(),
    )
    builder.button(
        text="🗑️ Удалить",
        callback_data=AdminAdsCallback(action="delete_confirm", code=code).pack(),
    )
    builder.row(build_admin_back_btn("ads"))
    builder.adjust(1)
    return builder.as_markup()


def build_ads_delete_confirm_kb(code: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, удалить",
        callback_data=AdminAdsCallback(
            action="delete",
            code=code,
        ).pack(),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=AdminAdsCallback(action="view", code=code).pack(),
    )
    builder.adjust(1)
    return builder.as_markup()


def build_cancel_input_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Отмена",
        callback_data=AdminAdsCallback(action="cancel_input", code="none").pack(),
    )
    return builder.as_markup()
