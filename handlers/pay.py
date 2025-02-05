from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    CRYPTO_BOT_ENABLE,
    DONATIONS_ENABLE,
    ROBOKASSA_ENABLE,
    STARS_ENABLE,
    YOOKASSA_ENABLE,
    YOOMONEY_ENABLE,
)

router = Router()


@router.callback_query(F.data == "pay")
async def handle_pay(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()

    if YOOKASSA_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text="💳 ЮКасса: быстрый перевод",
                callback_data="pay_yookassa",
            )
        )
    if YOOMONEY_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text="💳 ЮМани: оплата по карте",
                callback_data="pay_yoomoney",
            )
        )
    if CRYPTO_BOT_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text="💰 CryptoBot: криптовалюта",
                callback_data="pay_cryptobot",
            )
        )
    if STARS_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text="⭐ Оплата Звездами",
                callback_data="pay_stars",
            )
        )
    if ROBOKASSA_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text="⭐ RoboKassa",
                callback_data="pay_robokassa",
            )
        )

    builder.row(InlineKeyboardButton(text="🎟️ Активировать купон", callback_data="activate_coupon"))
    if DONATIONS_ENABLE:
        builder.row(InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate"))

    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await callback_query.message.answer(
        "💸 <b>Выберите удобный способ пополнения баланса:</b>\n"
        "<blockquote>"
        "• Быстро и безопасно\n"
        "• Поддержка разных платежных систем\n"
        "• Моментальное зачисление средств 🚀\n"
        "</blockquote>",
        reply_markup=builder.as_markup(),
    )
