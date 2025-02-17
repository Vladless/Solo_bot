from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class AdminPanelCallback(CallbackData, prefix="admin_panel"):
    action: str


def build_panel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Поиск пользователя", callback_data=AdminPanelCallback(action="search_user").pack())
    builder.button(text="🔑 Поиск по названию ключа", callback_data=AdminPanelCallback(action="search_key").pack())
    builder.row(
        InlineKeyboardButton(text="🖥️ Серверы", callback_data=AdminPanelCallback(action="servers").pack()),
        InlineKeyboardButton(text="🎟️ Купоны", callback_data=AdminPanelCallback(action="coupons").pack()),
    )
    builder.button(text="📢 Рассылка", callback_data=AdminPanelCallback(action="sender").pack())
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data=AdminPanelCallback(action="stats").pack()),
        InlineKeyboardButton(text="🤖 Управление", callback_data=AdminPanelCallback(action="management").pack()),
    )
    builder.button(text="Личный кабинет", callback_data="profile")
    builder.adjust(1, 1, 2, 1, 2, 1)
    return builder.as_markup()


def build_management_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💾 Создать резервную копию", callback_data=AdminPanelCallback(action="backups").pack())
    builder.button(text="🚫 Заблокировавшие бота", callback_data=AdminPanelCallback(action="bans").pack())
    builder.button(text="🔄 Перезагрузить бота", callback_data=AdminPanelCallback(action="restart").pack())
    builder.button(text="🌐 Сменить домен", callback_data=AdminPanelCallback(action="change_domain").pack())
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_restart_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, перезагрузить", callback_data=AdminPanelCallback(action="restart_confirm").pack())
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()


def build_admin_back_kb(action: str = "admin") -> InlineKeyboardMarkup:
    return build_admin_singleton_kb("🔙 Назад", action)


def build_admin_singleton_kb(text: str, action: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(build_admin_btn(text, action))
    return builder.as_markup()


def build_admin_back_btn(action: str = "admin") -> InlineKeyboardButton:
    return build_admin_btn("🔙 Назад", action)


def build_admin_btn(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=AdminPanelCallback(action=action).pack())
