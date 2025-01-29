from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.admin.panel_kb import build_admin_back_btn, AdminPanelCallback


def build_bans_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📄 Выгрузить в CSV", callback_data=AdminPanelCallback(action="bans_export").pack())
    builder.button(text="🗑️ Удалить из БД", callback_data=AdminPanelCallback(action="bans_delete_banned").pack())
    builder.row(build_admin_back_btn("management"))
    builder.adjust(1)
    return builder.as_markup()
