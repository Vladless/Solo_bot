import os

from typing import Any

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
from database import get_last_payments
from handlers.buttons import (
    BALANCE_HISTORY,
    COUPON,
    CRYPTOBOT,
    MAIN_MENU,
    PAYMENT,
    ROBOKASSA,
    STARS,
    YOOKASSA,
    YOOMONEY,
)
from handlers.texts import BALANCE_HISTORY_HEADER, BALANCE_MANAGEMENT_TEXT, PAYMENT_METHODS_MSG

from .utils import edit_or_send_message


router = Router()


@router.callback_query(F.data == "pay")
async def handle_pay(callback_query: CallbackQuery):
    builder = InlineKeyboardBuilder()

    if YOOKASSA_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text=YOOKASSA,
                callback_data="pay_yookassa",
            )
        )
    if YOOMONEY_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text=YOOMONEY,
                callback_data="pay_yoomoney",
            )
        )
    if CRYPTO_BOT_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text=CRYPTOBOT,
                callback_data="pay_cryptobot",
            )
        )
    if STARS_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text=STARS,
                callback_data="pay_stars",
            )
        )
    if ROBOKASSA_ENABLE:
        builder.row(
            InlineKeyboardButton(
                text=ROBOKASSA,
                callback_data="pay_robokassa",
            )
        )
    if DONATIONS_ENABLE:
        builder.row(InlineKeyboardButton(text="💰 Поддержать проект", callback_data="donate"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=PAYMENT_METHODS_MSG,
        reply_markup=builder.as_markup(),
        media_path=None,
        disable_web_page_preview=False,
    )


@router.callback_query(F.data == "balance")
async def balance_handler(callback_query: CallbackQuery, session: Any):
    result = await session.fetchrow(
        "SELECT balance FROM users WHERE tg_id = $1",
        callback_query.from_user.id,
    )
    balance = result["balance"] if result else 0.0
    balance = int(balance)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=BALANCE_HISTORY, callback_data="balance_history"))
    builder.row(InlineKeyboardButton(text=COUPON, callback_data="activate_coupon"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    text = BALANCE_MANAGEMENT_TEXT.format(balance=balance)
    image_path = os.path.join("img", "pic.jpg")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        disable_web_page_preview=False,
    )


@router.callback_query(F.data == "balance_history")
async def balance_history_handler(callback_query: CallbackQuery, session: Any):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    records = await get_last_payments(callback_query.from_user.id, session)

    if records:
        history_text = BALANCE_HISTORY_HEADER
        for record in records:
            amount = record["amount"]
            payment_system = record["payment_system"]
            status = record["status"]
            date = record["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            history_text += (
                f"<b>Сумма:</b> {amount}₽\n"
                f"<b>Способ оплаты:</b> {payment_system}\n"
                f"<b>Статус:</b> {status}\n"
                f"<b>Дата:</b> {date}\n\n"
            )
    else:
        history_text = "❌ У вас пока нет операций с балансом."

    await edit_or_send_message(
        target_message=callback_query.message,
        text=history_text,
        reply_markup=builder.as_markup(),
        media_path=None,
        disable_web_page_preview=False,
    )
