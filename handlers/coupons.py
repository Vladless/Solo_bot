from datetime import datetime

import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import DATABASE_URL
from database import update_balance
from logger import logger


class CouponActivationState(StatesGroup):
    waiting_for_coupon_code = State()


router = Router()


@router.callback_query(F.data == "activate_coupon")
async def handle_activate_coupon(
    callback_query: types.CallbackQuery, state: FSMContext
):
    try:
        await callback_query.message.delete()
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ Вернуться в профиль", callback_data="view_profile")
    )

    await callback_query.message.answer(
        "<b>Введите код купона:</b>\n\n"
        "Пожалуйста, введите действующий код купона, который вы хотите активировать.",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(CouponActivationState.waiting_for_coupon_code)
    await callback_query.answer()


@router.message(CouponActivationState.waiting_for_coupon_code)
async def process_coupon_code(message: types.Message, state: FSMContext):
    try:
        await message.delete()
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {e}")

    coupon_code = message.text.strip()
    activation_result = await activate_coupon(message.from_user.id, coupon_code)

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👤 Личный кабинет", callback_data="view_profile")
    )

    markup = builder.as_markup()

    await message.answer(activation_result, reply_markup=markup, parse_mode="HTML")
    await state.clear()


async def activate_coupon(user_id: int, coupon_code: str):
    """Функция для активации купона"""
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        coupon_record = await conn.fetchrow(
            """
            SELECT id, usage_limit, usage_count, is_used, amount
            FROM coupons
            WHERE code = $1 AND (usage_count < usage_limit OR usage_limit = 0) AND is_used = FALSE
        """,
            coupon_code,
        )

        if not coupon_record:
            return "<b>❌ Купон не найден</b> или его использование ограничено. Пожалуйста, проверьте код и попробуйте снова."

        usage_exists = await conn.fetchrow(
            """
            SELECT 1 FROM coupon_usages WHERE coupon_id = $1 AND user_id = $2
        """,
            coupon_record["id"],
            user_id,
        )

        if usage_exists:
            return "<b>❌ Вы уже активировали этот купон.</b> Купоны могут быть активированы только один раз."

        coupon_amount = coupon_record["amount"]

        async with conn.transaction():
            await conn.execute(
                """
                UPDATE coupons
                SET usage_count = usage_count + 1,
                    is_used = CASE WHEN usage_count + 1 >= usage_limit AND usage_limit > 0 THEN TRUE ELSE FALSE END
                WHERE id = $1
            """,
                coupon_record["id"],
            )

            await conn.execute(
                """
                INSERT INTO coupon_usages (coupon_id, user_id, used_at)
                VALUES ($1, $2, $3)
            """,
                coupon_record["id"],
                user_id,
                datetime.utcnow(),
            )

        await update_balance(user_id, coupon_amount)
        return f"<b>✅ Купон успешно активирован!</b>\n\nНа ваш баланс добавлено <b>{coupon_amount} рублей</b>."

    except Exception as e:
        logger.error(f"Ошибка при активации купона: {e}")
        return (
            "<b>⚠️ Произошла ошибка при активации купона.</b>\nПопробуйте ещё раз позже."
        )

    finally:
        await conn.close()
