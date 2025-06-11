from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.models import Tariff, Gift
from ..panel.keyboard import AdminPanelCallback

from handlers.buttons import BACK

def build_admin_gifts_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🎁 Создать подарок", callback_data="admin_gift_create")
    )
    builder.row(
        InlineKeyboardButton(text="📦 Все подарки", callback_data="admin_gifts_all"),
    )
    builder.row(
        InlineKeyboardButton(text=BACK, callback_data="admin")
    )

    return builder.as_markup()


def build_gift_tariffs_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(
            text=f"{tariff.name} — {tariff.duration_days // 30} мес.",
            callback_data=f"admin_gift_confirm|{tariff.id}",
        )
    builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="gifts").pack())
    builder.adjust(1)
    return builder.as_markup()


def build_gifts_list_kb(gifts: list[Gift], page: int, total: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for gift in gifts:
        button_text = f"{gift.gift_id[:6]}... — {gift.selected_months} мес."
        builder.button(
            text=button_text,
            callback_data=f"gift_view|{gift.gift_id}",
        )

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"gifts_page|{page - 1}"))
    if len(gifts) == 10:
        nav.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"gifts_page|{page + 1}"))

    builder.row(*nav)
    builder.button(text="🔙 Назад", callback_data=AdminPanelCallback(action="gifts").pack())

    return builder.as_markup()