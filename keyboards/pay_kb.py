from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import STARS_ENABLE, ROBOKASSA_ENABLE, CRYPTO_BOT_ENABLE, FREEKASSA_ENABLE, YOOKASSA_ENABLE


def build_pay_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if YOOKASSA_ENABLE:
        builder.button(
            text="💳 ЮКасса: быстрый перевод",
            callback_data="pay_yookassa",
        )
    if FREEKASSA_ENABLE:
        builder.button(
            text="🌐 FreeKassa: множество способов",
            callback_data="pay_freekassa",
        )
    if CRYPTO_BOT_ENABLE:
        builder.button(
            text="💰 CryptoBot: криптовалюта",
            callback_data="pay_cryptobot",
        )
    if STARS_ENABLE:
        builder.button(
            text="⭐ Оплата Звездами",
            callback_data="pay_stars",
        )
    if ROBOKASSA_ENABLE:
        builder.button(
            text="⭐ RoboKassa",
            callback_data="pay_robokassa",
        )

    builder.button(text="🎟️ Активировать купон", callback_data="activate_coupon")
    builder.button(text="💰 Поддержать проект", callback_data="donate")
    builder.button(text="👤 Личный кабинет", callback_data="profile")

    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)
