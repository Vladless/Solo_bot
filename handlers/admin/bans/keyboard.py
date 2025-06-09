from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


def build_bans_kb():
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📛 Забанившие бота",
        callback_data=AdminPanelCallback(action="bans_export").pack(),
    )
    builder.button(
        text="📛 Забаненные вручную",
        callback_data=AdminPanelCallback(action="manual_bans_export").pack(),
    )
    builder.button(
        text="🗑️ Удалить забанивших",
        callback_data=AdminPanelCallback(action="bans_delete_banned").pack(),
    )
    builder.button(
        text="🗑️ Очистить вручную забаненных",
        callback_data=AdminPanelCallback(action="bans_delete_manual").pack(),
    )
    builder.button(
        text="🔙 Назад", callback_data=AdminPanelCallback(action="management").pack()
    )

    builder.adjust(1)
    return builder.as_markup()
