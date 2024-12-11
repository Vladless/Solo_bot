from datetime import datetime
from typing import Any

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import update_balance
from keyboards.profile_kb import build_profile_back_kb


class CouponActivationState(StatesGroup):
    waiting_for_coupon_code = State()


router = Router()


@router.callback_query(F.data == "activate_coupon")
async def handle_activate_coupon(callback_query: types.CallbackQuery, state: FSMContext):
    # Prepare text
    text = (
        "<b>🎫 Введите код купона:</b>\n\n"
        "📝 Пожалуйста, введите действующий код купона, который вы хотите активировать. 🔑"
    )

    # Build keyboard
    kb = build_profile_back_kb()

    # Answer message
    await callback_query.message.answer(
        text=text,
        reply_markup=kb,
    )

    # Set state
    await state.set_state(CouponActivationState.waiting_for_coupon_code)


@router.message(CouponActivationState.waiting_for_coupon_code)
async def process_coupon_code(message: types.Message, state: FSMContext, session: Any):
    coupon_code = message.text.strip()
    activation_result = await activate_coupon(message.chat.id, coupon_code, session)

    # Build keyboard
    kb = build_profile_back_kb()

    # Answer message
    await message.answer(
        text=activation_result,
        reply_markup=kb,
        parse_mode="HTML",
    )

    # Clear the state
    await state.clear()


async def activate_coupon(user_id: int, coupon_code: str, session: Any):
    coupon_record = await session.fetchrow(
        """
        SELECT id, usage_limit, usage_count, is_used, amount
        FROM coupons
        WHERE code = $1 AND (usage_count < usage_limit OR usage_limit = 0) AND is_used = FALSE
    """,
        coupon_code,
    )

    if not coupon_record:
        return "<b>❌ Купон не найден</b> 🚫 или его использование ограничено. 🔒 Пожалуйста, проверьте код и попробуйте снова. 🔍"

    usage_exists = await session.fetchrow(
        """
        SELECT 1 FROM coupon_usages WHERE coupon_id = $1 AND user_id = $2
    """,
        coupon_record["id"],
        user_id,
    )

    if usage_exists:
        return "<b>❌ Вы уже активировали этот купон.</b> 🚫 Купоны могут быть активированы только один раз. 🔒"

    coupon_amount = coupon_record["amount"]

    async with session.transaction():
        await session.execute(
            """
            UPDATE coupons
            SET usage_count = usage_count + 1,
                is_used = CASE WHEN usage_count + 1 >= usage_limit AND usage_limit > 0 THEN TRUE ELSE FALSE END
            WHERE id = $1
        """,
            coupon_record["id"],
        )

        await session.execute(
            """
            INSERT INTO coupon_usages (coupon_id, user_id, used_at)
            VALUES ($1, $2, $3)
        """,
            coupon_record["id"],
            user_id,
            datetime.utcnow(),
        )

    await update_balance(user_id, coupon_amount)
    return f"<b>✅ Купон успешно активирован! 🎉</b>\n\nНа ваш баланс добавлено <b>{coupon_amount} рублей</b> 💰."
