from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.utils import format_days
from settings.buttons import BACK

from ..panel.keyboard import AdminPanelCallback, build_admin_back_btn


class AdminCouponDeleteCallback(CallbackData, prefix="admin_coupon_delete"):
    coupon_code: str
    confirm: bool | None = None


def build_coupons_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="➕ Создать купон",
            callback_data=AdminPanelCallback(action="coupons_create").pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="Купоны",
            callback_data=AdminPanelCallback(action="coupons_list").pack(),
        )
    )
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
    text = "📜 <b>Список купонов</b>\n\n"

    for i, coupon in enumerate(coupons, start=1):
        percent_value = coupon.get("percent")
        days_value = coupon.get("days")
        amount_value = coupon.get("amount") or 0

        if percent_value is not None and int(percent_value) > 0:
            value_line = f"📉 <b>Скидка:</b> {int(percent_value)}%"
        elif days_value is not None and int(days_value) > 0:
            value_line = f"⏳ <b>Продление:</b> {format_days(int(days_value))}"
        elif int(amount_value) > 0:
            value_line = f"💰 <b>Баланс:</b> {int(amount_value)} ₽"
        else:
            value_line = "—"

        text += (
            f"<blockquote>"
            f"<b>{i}. {coupon['code']}</b>\n"
            f"{value_line}\n"
            f"🔢 <b>Лимит:</b> {coupon['usage_limit']} | "
            f"✅ <b>Использовано:</b> {coupon['usage_count']}\n"
            f"<code>https://telegram.me/{username_bot}?start=coupons_{coupon['code']}</code>"
            f"</blockquote>\n\n"
        )

    return text
