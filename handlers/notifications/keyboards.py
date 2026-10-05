from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from handlers.keys.utils import build_key_callback
from settings.buttons import (
    ADD_SUB,
    CHANGE_TARIFF,
    DISCOUNT_TARIFF,
    MAIN_MENU,
    MAX_DISCOUNT_TARIFF,
    RENEW_KEY_NOTIFICATION,
)


def build_notification_kb(email: str, client_id: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=RENEW_KEY_NOTIFICATION, callback_data=build_key_callback("renew_key", client_id, email))
    builder.button(text=MAIN_MENU, callback_data="profile")
    builder.adjust(1)
    return builder.as_markup()


def build_change_tariff_kb(email: str, client_id: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=CHANGE_TARIFF, callback_data=build_key_callback("renew_key", client_id, email))
    builder.button(text=MAIN_MENU, callback_data="profile")
    builder.adjust(1)
    return builder.as_markup()


def build_notification_expired_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=MAIN_MENU, callback_data="profile")
    return builder.as_markup()


def build_hot_lead_kb(final: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=DISCOUNT_TARIFF if not final else MAX_DISCOUNT_TARIFF,
                    callback_data=("hot_lead_discount" if not final else "hot_lead_final_discount"),
                )
            ]
        ]
    )


def build_cold_lead_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=ADD_SUB, callback_data="create_key")]])


def build_cold_lead_discount_kb(final: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=DISCOUNT_TARIFF if not final else MAX_DISCOUNT_TARIFF,
                    callback_data=("cold_lead_discount" if not final else "cold_lead_final_discount"),
                )
            ]
        ]
    )


def build_tariffs_keyboard(tariffs: list[dict], prefix: str = "tariff") -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{t['name']} — {t['price_rub']}₽",
                callback_data=f"{prefix}|{t['id']}",
            )
        ]
        for t in tariffs
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
