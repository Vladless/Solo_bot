from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import AdminPanelCallback


def build_bans_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📛 Забанившие бота",
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
    builder.button(
        text="🔙 Назад",
        callback_data=AdminPanelCallback(action="management").pack(),
    )

    builder.adjust(1)
    return builder.as_markup()


def build_blocked_users_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📥 Экспорт",
        callback_data=AdminPanelCallback(action="bans_export").pack(),
    )
    builder.button(
        text="🗑️ Удалить забанивших",
        callback_data=AdminPanelCallback(action="bans_delete_banned").pack(),
    )
    builder.button(
        text="🗑️ Очистить забанивших",
        callback_data=AdminPanelCallback(action="bans_clear_blocked").pack(),
    )
    builder.button(
        text="🔙 Назад",
        callback_data=AdminPanelCallback(action="bans").pack(),
    )

    builder.adjust(1)
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
        text="🗑️ Очистить теневые баны",
        callback_data=AdminPanelCallback(action="bans_clear_shadow").pack(),
    )
    builder.button(
        text="🔙 Назад",
        callback_data=AdminPanelCallback(action="bans").pack(),
    )

    builder.adjust(1)
    return builder.as_markup()


def build_manual_bans_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📥 Экспорт",
        callback_data=AdminPanelCallback(action="manual_bans_export").pack(),
    )
    builder.button(
        text="🗑️ Очистить ручные баны",
        callback_data=AdminPanelCallback(action="bans_clear_manual").pack(),
    )
    builder.button(
        text="🔙 Назад",
        callback_data=AdminPanelCallback(action="bans").pack(),
    )

    builder.adjust(1)
    return builder.as_markup()
