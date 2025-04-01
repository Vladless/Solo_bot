from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import (
    check_coupon_usage,
    create_coupon_usage,
    get_coupon_by_code,
    update_balance,
    update_coupon_usage_count,
)
from handlers.buttons import MAIN_MENU
from handlers.texts import (
    COUPON_ACTIVATED_SUCCESS_MSG,
    COUPON_ALREADY_USED_MSG,
    COUPON_INPUT_PROMPT,
    COUPON_NOT_FOUND_MSG,
)

from .utils import edit_or_send_message


class CouponActivationState(StatesGroup):
    waiting_for_coupon_code = State()


router = Router()


@router.callback_query(F.data == "activate_coupon")
@router.message(F.text == "/activate_coupon")
async def handle_activate_coupon(callback_query_or_message: Message | CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if isinstance(callback_query_or_message, CallbackQuery):
        target_message = callback_query_or_message.message
    else:
        target_message = callback_query_or_message

    await edit_or_send_message(
        target_message=target_message,
        text=COUPON_INPUT_PROMPT,
        reply_markup=builder.as_markup(),
        media_path=None,
    )
    await state.set_state(CouponActivationState.waiting_for_coupon_code)


@router.message(CouponActivationState.waiting_for_coupon_code)
async def process_coupon_code(message: Message, state: FSMContext, session: Any):
    coupon_code = message.text.strip()
    activation_result = await activate_coupon(message.chat.id, coupon_code, session)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await message.answer(activation_result, reply_markup=builder.as_markup())

    if activation_result.startswith(COUPON_ACTIVATED_SUCCESS_MSG.split('.')[0]):
        await state.clear()


async def activate_coupon(user_id: int, coupon_code: str, session: Any):
    coupon_record = await get_coupon_by_code(coupon_code, session)

    if not coupon_record:
        return COUPON_NOT_FOUND_MSG

    usage_exists = await check_coupon_usage(coupon_record["id"], user_id, session)

    if usage_exists:
        return COUPON_ALREADY_USED_MSG

    coupon_amount = coupon_record["amount"]

    await update_coupon_usage_count(coupon_record["id"], session)
    await create_coupon_usage(coupon_record["id"], user_id, session)

    await update_balance(user_id, coupon_amount, session)
    return COUPON_ACTIVATED_SUCCESS_MSG.format(coupon_amount=coupon_amount)
