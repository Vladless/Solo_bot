from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.buttons import BACK
from handlers.utils import format_days

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


class AdminCouponDeleteCallback(CallbackData, prefix="admin_coupon_delete"):
    coupon_code: str
    confirm: Optional[bool] = None


def build_coupons_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать купон", callback_data=AdminPanelCallback(action="coupons_create").pack())
    builder.button(text="Купоны", callback_data=AdminPanelCallback(action="coupons_list").pack())
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def build_coupons_list_kb(coupons: list, current_page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for coupon in coupons:
        coupon_code = coupon["code"]
        builder.button(
            text=f"❌{coupon_code}",
            callback_data=AdminCouponDeleteCallback(coupon_code=coupon_code).pack(),
        )

    pagination_buttons = []
    if current_page > 1:
        pagination_buttons.append(
            InlineKeyboardButton(
                text=BACK,
                callback_data=AdminPanelCallback(action="coupons_list", page=current_page - 1).pack(),
            )
        )
    if current_page < total_pages:
        pagination_buttons.append(
            InlineKeyboardButton(
                text="Вперед ➡️",
                callback_data=AdminPanelCallback(action="coupons_list", page=current_page + 1).pack(),
            )
        )
    if pagination_buttons:
        builder.row(*pagination_buttons)

    builder.row(build_admin_back_btn("coupons"))
    builder.adjust(2)
    return builder.as_markup()


def format_coupons_list(coupons: list, username_bot: str) -> str:
    coupon_list = "📜 Список всех купонов:\n\n"
    for coupon in coupons:
        value_text = f"💰 <b>Сумма:</b> {coupon['amount']} рублей" if coupon["amount"] > 0 else f"⏳ <b>{format_days(coupon['days'])}</b>"
        coupon_list += (
            f"🏷️ <b>Код:</b> {coupon['code']}\n"
            f"{value_text}\n"
            f"🔢 <b>Лимит использования:</b> {coupon['usage_limit']} раз\n"
            f"✅ <b>Использовано:</b> {coupon['usage_count']} раз\n"
            f"🔗 <b>Ссылка:</b> <code>https://t.me/{username_bot}?start=coupons_{coupon['code']}</code>\n\n"
        )
    return coupon_list
