from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


def build_stats_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=AdminPanelCallback(action="stats").pack())
    builder.button(
        text="📥 Выгрузить пользователей в CSV",
        callback_data=AdminPanelCallback(action="stats_export_users_csv").pack(),
    )
    builder.button(
        text="📥 Выгрузить оплаты в CSV", callback_data=AdminPanelCallback(action="stats_export_payments_csv").pack()
    )
    builder.button(
        text="📥 Выгрузить подписки в CSV",
        callback_data=AdminPanelCallback(action="stats_export_keys_csv").pack(),
    )
    builder.button(
        text="📥 Выгрузить горящих лидов", callback_data=AdminPanelCallback(action="stats_export_hot_leads_csv").pack()
    )
    builder.row(build_admin_back_btn())
    builder.adjust(1)
    return builder.as_markup()
