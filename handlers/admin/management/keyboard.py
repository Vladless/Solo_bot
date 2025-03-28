from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


def build_management_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💾 Создать резервную копию", callback_data=AdminPanelCallback(action="backups").pack())
    builder.button(text="🚫 Заблокировавшие бота", callback_data=AdminPanelCallback(action="bans").pack())
    builder.button(text="🔄 Перезагрузить бота", callback_data=AdminPanelCallback(action="restart").pack())
    builder.button(text="🌐 Сменить домен", callback_data=AdminPanelCallback(action="change_domain").pack())
    builder.button(text="🔑 Восстановить пробники", callback_data=AdminPanelCallback(action="restore_trials").pack())
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()
