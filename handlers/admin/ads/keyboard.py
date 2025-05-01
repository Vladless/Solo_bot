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

    for ad in ads:
        builder.button(
            text=f"📎 {ad['name']}",
            callback_data=AdminAdsCallback(action="view", code=ad["code"]).pack(),
        )

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
    builder.button(text="🗑️ Удалить", callback_data=AdminAdsCallback(action="delete_confirm", code=code).pack())
    builder.row(build_admin_back_btn("ads"))
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
    builder.button(text="❌ Отмена", callback_data=AdminAdsCallback(action="view", code=code).pack())
    builder.adjust(1)
    return builder.as_markup()


def build_cancel_input_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=AdminAdsCallback(action="cancel_input", code="none").pack())
    return builder.as_markup()
