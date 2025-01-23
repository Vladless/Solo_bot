from datetime import datetime
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import (
    check_coupon_usage,
    create_coupon_usage,
    get_coupon_by_code,
    update_balance,
    update_coupon_usage_count,
)


class CouponActivationState(StatesGroup):
    waiting_for_coupon_code = State()


router = Router()


@router.callback_query(F.data == "activate_coupon")
async def handle_activate_coupon(callback_query: types.CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await callback_query.message.answer(
        "<b>🎫 Введите код купона:</b>\n\n"
        "📝 Пожалуйста, введите действующий код купона, который вы хотите активировать. 🔑",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(CouponActivationState.waiting_for_coupon_code)


@router.message(CouponActivationState.waiting_for_coupon_code)
async def process_coupon_code(message: types.Message, state: FSMContext, session: Any):
    coupon_code = message.text.strip()
    activation_result = await activate_coupon(message.chat.id, coupon_code, session)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="👤 Личный кабинет", callback_data="profile"))

    await message.answer(activation_result, reply_markup=builder.as_markup())
    await state.clear()


async def activate_coupon(user_id: int, coupon_code: str, session: Any):
    coupon_record = await get_coupon_by_code(coupon_code, session)

    if not coupon_record:
        return "<b>❌ Купон не найден</b> 🚫 или его использование ограничено. 🔒 Пожалуйста, проверьте код и попробуйте снова. 🔍"

    usage_exists = await check_coupon_usage(coupon_record["id"], user_id, session)

    if usage_exists:
        return "<b>❌ Вы уже активировали этот купон.</b> 🚫 Купоны могут быть активированы только один раз. 🔒"

    coupon_amount = coupon_record["amount"]

    await update_coupon_usage_count(coupon_record["id"], session)
    await create_coupon_usage(coupon_record["id"], user_id, session)

    await update_balance(user_id, coupon_amount, session)
    return f"<b>✅ Купон успешно активирован! 🎉</b>\n\nНа ваш баланс добавлено <b>{coupon_amount} рублей</b> 💰."
