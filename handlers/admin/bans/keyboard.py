from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from settings.buttons import BACK

from ..panel.keyboard import AdminPanelCallback


def build_bans_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📛 Забанившие",
        callback_data=AdminPanelCallback(action="bans_blocked_menu").pack(),
    )
    builder.button(
        text="👻 Теневые баны",
        callback_data=AdminPanelCallback(action="bans_shadow_menu").pack(),
    )
    builder.button(
        text="🔒 Ручные баны",
        callback_data=AdminPanelCallback(action="bans_manual_menu").pack(),
    )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="management").pack()))
    return builder.as_markup()


def build_blocked_users_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📥 Экспорт",
        callback_data=AdminPanelCallback(action="bans_export").pack(),
    )
    builder.button(
        text="🗑 Удалить клиентов",
        callback_data=AdminPanelCallback(action="bans_delete_banned").pack(),
    )
    builder.button(
        text="🧹 Очистить список",
        callback_data=AdminPanelCallback(action="bans_clear_blocked").pack(),
    )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="bans").pack()))
    return builder.as_markup()


def build_shadow_bans_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📥 Экспорт",
        callback_data=AdminPanelCallback(action="shadow_bans_export").pack(),
    )
    builder.button(
        text="➕ Забанить заранее",
        callback_data=AdminPanelCallback(action="bans_preemptive").pack(),
    )
    builder.button(
        text="🧹 Очистить список",
        callback_data=AdminPanelCallback(action="bans_clear_shadow").pack(),
    )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="bans").pack()))
    return builder.as_markup()


def build_manual_bans_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📥 Экспорт",
        callback_data=AdminPanelCallback(action="manual_bans_export").pack(),
    )
    builder.button(
        text="🧹 Очистить список",
        callback_data=AdminPanelCallback(action="bans_clear_manual").pack(),
    )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=BACK, callback_data=AdminPanelCallback(action="bans").pack()))
    return builder.as_markup()
