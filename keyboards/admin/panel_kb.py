from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


class AdminPanelCallback(CallbackData, prefix='admin_panel'):
    action: str


def build_panel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔍 Поиск пользователя",
        callback_data=AdminPanelCallback(action="search_user").pack()
    )
    builder.button(
        text="🔑 Поиск по названию ключа",
        callback_data=AdminPanelCallback(action="search_key").pack()
    )
    builder.row(
        InlineKeyboardButton(
            text="🖥️ Серверы",
            callback_data=AdminPanelCallback(action="servers").pack()
        ),
        InlineKeyboardButton(
            text="🎟️ Купоны",
            callback_data=AdminPanelCallback(action="coupons").pack()
        )
    )
    builder.button(
        text="📢 Рассылка",
        callback_data=AdminPanelCallback(action="sender").pack()
    )
    builder.button(
        text="🤖 Управление Ботом",
        callback_data=AdminPanelCallback(action="management").pack()
    )
    builder.button(
        text="📊 Статистика",
        callback_data=AdminPanelCallback(action="stats").pack()
    )
    builder.button(
        text="👤 Личный кабинет",
        callback_data="profile"
    )
    return builder.as_markup()


def build_management_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💾 Создать резервную копию",
        callback_data=AdminPanelCallback(action="backups").pack()
    )
    builder.button(
        text="🚫 Баны",
        callback_data=AdminPanelCallback(action="bans").pack()
    )
    builder.button(
        text="🔄 Перезагрузить бота",
        callback_data=AdminPanelCallback(action="restart").pack()
    )
    builder.button(
        text="⬅️ Назад",
        callback_data="admin"
    )
    return builder.as_markup()


def build_restart_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Да, перезапустить",
        callback_data=AdminPanelCallback(action="restart_confirm").pack()
    )
    builder.button(
        text="🔙 Назад",
        callback_data="admin"
    )
    return builder.as_markup()
