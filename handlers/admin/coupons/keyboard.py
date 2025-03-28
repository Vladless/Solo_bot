from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.buttons import BACK

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


class AdminCouponDeleteCallback(CallbackData, prefix="admin_coupon_delete"):
    coupon_code: str


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
