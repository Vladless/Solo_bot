from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_btn


class AdminCouponDeleteCallback(CallbackData, prefix="admin_coupon_delete"):
    coupon_code: str


def build_coupons_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Создать купон", callback_data=AdminPanelCallback(action="coupons_create").pack())
    builder.button(text="Купоны", callback_data=AdminPanelCallback(action="coupons_list").pack())
    builder.row(build_admin_back_btn())
    return builder.as_markup()


def build_coupons_list_kb(coupons: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for coupon in coupons:
        coupon_code = coupon["code"]
        builder.button(
            text=f"❌{coupon_code}",
            callback_data=AdminCouponDeleteCallback(coupon_code=coupon_code).pack(),
        )

    builder.row(build_admin_back_btn("coupons"))
    builder.adjust(2)
    return builder.as_markup()
