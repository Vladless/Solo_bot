from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.utils import format_days
from settings.buttons import BACK

from ..panel.headers import card, section
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
    builder.adjust(1)
    return builder.as_markup()


def format_coupons_list(coupons: list, username_bot: str) -> str:
    """Возвращает секции с купонами страницы."""
    blocks = []
    for coupon in coupons:
        percent_value = coupon.get("percent")
        days_value = coupon.get("days")
        amount_value = coupon.get("amount") or 0

        if percent_value is not None and int(percent_value) > 0:
            value_line = f"Скидка: {int(percent_value)}%"
        elif days_value is not None and int(days_value) > 0:
            value_line = f"Продление: {format_days(int(days_value))}"
        elif int(amount_value) > 0:
            value_line = f"Баланс: {int(amount_value)} ₽"
        else:
            value_line = "Награда: —"

        blocks.append(
            section(
                f"🎟 {coupon['code']}",
                value_line,
                f"Лимит: {coupon['usage_limit']}",
                f"Выдано: {coupon['usage_count']}",
                f"https://telegram.me/{username_bot}?start=coupons_{coupon['code']}",
            )
        )

    return card(*blocks)
