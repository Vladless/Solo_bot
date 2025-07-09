import os
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    CRYPTO_BOT_ENABLE,
    DONATIONS_ENABLE,
    FREEKASSA_ENABLE,
    ROBOKASSA_ENABLE,
    STARS_ENABLE,
    YOOKASSA_ENABLE,
    YOOMONEY_ENABLE,
)
from database import get_last_payments
from database.models import User
from handlers.localization import get_user_texts, get_user_buttons
from handlers.payments.cryprobot_pay import process_callback_pay_cryptobot
from handlers.payments.freekassa_pay import process_callback_pay_freekassa
from handlers.payments.robokassa_pay import process_callback_pay_robokassa
from handlers.payments.stars_pay import process_callback_pay_stars
from handlers.payments.yookassa_pay import process_callback_pay_yookassa
from handlers.payments.yoomoney_pay import process_callback_pay_yoomoney

from .utils import edit_or_send_message

router = Router()


@router.callback_query(F.data == "pay")
async def handle_pay(
    callback_query: CallbackQuery, state: FSMContext, session: AsyncSession
):
    # Получаем локализованные тексты и кнопки для пользователя
    texts = await get_user_texts(session, callback_query.from_user.id)
    buttons = await get_user_buttons(session, callback_query.from_user.id)

    payment_handlers = []

    if YOOKASSA_ENABLE:
        payment_handlers.append(process_callback_pay_yookassa)
    if YOOMONEY_ENABLE:
        payment_handlers.append(process_callback_pay_yoomoney)
    if CRYPTO_BOT_ENABLE:
        payment_handlers.append(process_callback_pay_cryptobot)
    if STARS_ENABLE:
        payment_handlers.append(process_callback_pay_stars)
    if ROBOKASSA_ENABLE:
        payment_handlers.append(process_callback_pay_robokassa)
    if FREEKASSA_ENABLE:
        payment_handlers.append(process_callback_pay_freekassa)

    if len(payment_handlers) == 1:
        await callback_query.answer()
        return await payment_handlers[0](callback_query, state, session)

    builder = InlineKeyboardBuilder()

    if YOOKASSA_ENABLE:
        builder.row(InlineKeyboardButton(text=buttons.YOOKASSA, callback_data="pay_yookassa"))
    if YOOMONEY_ENABLE:
        builder.row(InlineKeyboardButton(text=buttons.YOOMONEY, callback_data="pay_yoomoney"))
    if CRYPTO_BOT_ENABLE:
        builder.row(InlineKeyboardButton(text=buttons.CRYPTOBOT, callback_data="pay_cryptobot"))
    if STARS_ENABLE:
        builder.row(InlineKeyboardButton(text=buttons.STARS, callback_data="pay_stars"))
    if ROBOKASSA_ENABLE:
        builder.row(InlineKeyboardButton(text=buttons.ROBOKASSA, callback_data="pay_robokassa"))
    if FREEKASSA_ENABLE:
        builder.row(InlineKeyboardButton(text=buttons.FREEKASSA, callback_data="pay_freekassa"))
    if DONATIONS_ENABLE:
        builder.row(
            InlineKeyboardButton(text=buttons.DONATE_PROJECT, callback_data="donate")
        )

    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=texts.PAYMENT_METHODS_MSG,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "balance")
async def balance_handler(callback_query: CallbackQuery, session: AsyncSession):
    # Получаем локализованные тексты и кнопки для пользователя
    texts = await get_user_texts(session, callback_query.from_user.id)
    buttons = await get_user_buttons(session, callback_query.from_user.id)
    
    stmt = select(User.balance).where(User.tg_id == callback_query.from_user.id)
    result = await session.execute(stmt)
    balance = result.scalar_one_or_none() or 0.0
    balance = int(balance)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=buttons.PAYMENT, callback_data="pay"))
    builder.row(
        InlineKeyboardButton(text=buttons.BALANCE_HISTORY, callback_data="balance_history")
    )
    builder.row(InlineKeyboardButton(text=buttons.COUPON, callback_data="activate_coupon"))
    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    text = texts.BALANCE_MANAGEMENT_TEXT.format(balance=balance)
    image_path = os.path.join("img", "pay.jpg")

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
        media_path=image_path,
        disable_web_page_preview=False,
    )


@router.callback_query(F.data == "balance_history")
async def balance_history_handler(callback_query: CallbackQuery, session: Any):
    # Получаем локализованные тексты и кнопки для пользователя
    texts = await get_user_texts(session, callback_query.from_user.id)
    buttons = await get_user_buttons(session, callback_query.from_user.id)
    
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=buttons.PAYMENT, callback_data="pay"))
    builder.row(InlineKeyboardButton(text=buttons.MAIN_MENU, callback_data="profile"))

    records = await get_last_payments(session, callback_query.from_user.id)

    if records:
        history_text = texts.BALANCE_HISTORY_HEADER
        for record in records:
            amount = record["amount"]
            payment_system = record["payment_system"]
            status = record["status"]
            date = record["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            history_text += texts.BALANCE_HISTORY_ITEM.format(
                amount=amount,
                payment_system=payment_system,
                status=status,
                date=date
            )
        history_text += "</blockquote>"
    else:
        history_text = texts.NO_BALANCE_OPERATIONS

    await edit_or_send_message(
        target_message=callback_query.message,
        text=history_text,
        reply_markup=builder.as_markup(),
        media_path=None,
        disable_web_page_preview=False,
    )
