import html

from datetime import datetime
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_ID
from database import (
    add_user,
    check_coupon_usage,
    check_user_exists,
    create_coupon_usage,
    get_coupon_by_code,
    get_keys,
    update_balance,
    update_coupon_usage_count,
    update_key_expiry,
)
from handlers.buttons import MAIN_MENU
from handlers.keys.key_utils import renew_key_in_cluster
from handlers.profile import process_callback_view_profile
from handlers.texts import (
    COUPON_ALREADY_USED_MSG,
    COUPON_INPUT_PROMPT,
    COUPON_NOT_FOUND_MSG,
)
from handlers.utils import edit_or_send_message, format_days
from logger import logger


class CouponActivationState(StatesGroup):
    waiting_for_coupon_code = State()
    waiting_for_key_selection = State()


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
    await activate_coupon(message, state, session, coupon_code=coupon_code)


async def activate_coupon(message: Message, state: FSMContext, session: Any, coupon_code: str, admin: bool = False):
    logger.info(f"Активация купона: {coupon_code}")
    coupon_record = await get_coupon_by_code(coupon_code, session)

    if not coupon_record:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="exit_coupon_input"))
        await message.answer(COUPON_NOT_FOUND_MSG, reply_markup=builder.as_markup())
        return

    if coupon_record["usage_count"] >= coupon_record["usage_limit"] or coupon_record["is_used"]:
        await message.answer("❌ Лимит активаций купона исчерпан.")
        await state.clear()
        return

    user_id = message.chat.id

    usage = await check_coupon_usage(coupon_record["id"], user_id, session)
    if usage:
        await message.answer(COUPON_ALREADY_USED_MSG)
        await state.clear()
        return

    user_exists = await check_user_exists(user_id)
    if not user_exists:
        from_user = message.from_user
        await add_user(
            tg_id=from_user.id,
            username=from_user.username,
            first_name=from_user.first_name,
            last_name=from_user.last_name,
            language_code=from_user.language_code,
            is_bot=from_user.is_bot,
            session=session,
        )

    if coupon_record["amount"] > 0:
        try:
            await update_balance(user_id, coupon_record["amount"], session, skip_referral=True)
            await update_coupon_usage_count(coupon_record["id"], session)
            await create_coupon_usage(coupon_record["id"], user_id, session)
            await message.answer(f"✅ Купон активирован, на баланс начислено {coupon_record['amount']} рублей.")
            await state.clear()
        except Exception as e:
            logger.error(f"Ошибка при активации купона на баланс: {e}")
            await message.answer("❌ Ошибка при активации купона.")
            await state.clear()
        return

    if coupon_record["days"] is not None and coupon_record["days"] > 0:
        try:
            keys = await get_keys(user_id, session)
            active_keys = [k for k in keys if not k["is_frozen"]]

            if not active_keys:
                await message.answer("❌ У вас нет активных подписок для продления.")
                await state.clear()
                return

            builder = InlineKeyboardBuilder()
            moscow_tz = pytz.timezone("Europe/Moscow")
            response_message = "<b>🔑 Выберите подписку для продления:</b>\n\n<blockquote>"

            for key in active_keys:
                alias = key.get("alias")
                email = key["email"]
                client_id = key["client_id"]
                expiry_time = key.get("expiry_time")

                key_display = html.escape(alias.strip() if alias else email)
                expiry_date = datetime.fromtimestamp(expiry_time / 1000, tz=moscow_tz).strftime("до %d.%m.%y, %H:%M")
                response_message += f"• <b>{key_display}</b> ({expiry_date})\n"
                builder.button(text=key_display, callback_data=f"extend_key|{client_id}|{coupon_record['id']}")

            response_message += "</blockquote>"
            builder.button(text="Отмена", callback_data="cancel_coupon_activation")
            builder.adjust(1)

            await message.answer(response_message, reply_markup=builder.as_markup())
            await state.set_state(CouponActivationState.waiting_for_key_selection)
            await state.update_data(coupon_id=coupon_record["id"], user_id=user_id)
        except Exception as e:
            logger.error(f"Ошибка при обработке купона на дни: {e}")
            await message.answer("❌ Ошибка при активации купона.")
            await state.clear()
        return

    await message.answer("❌ Купон недействителен (нет суммы или дней).")
    await state.clear()


@router.callback_query(F.data.startswith("extend_key|"))
async def handle_key_extension(callback_query: CallbackQuery, state: FSMContext, session: Any, admin: bool = False):
    parts = callback_query.data.split("|")
    client_id = parts[1]
    coupon_id = int(parts[2])

    try:
        coupon = await session.fetchrow("SELECT * FROM coupons WHERE id = $1", coupon_id)
        if not coupon or coupon["usage_count"] >= coupon["usage_limit"]:
            await callback_query.message.edit_text("❌ Купон недействителен или лимит исчерпан.")
            await state.clear()
            return

        usage = await check_coupon_usage(coupon_id, callback_query.from_user.id, session)
        if usage:
            await callback_query.message.edit_text("❌ Вы уже активировали этот купон.")
            await state.clear()
            return

        key = await session.fetchrow(
            "SELECT * FROM keys WHERE tg_id = $1 AND client_id = $2", callback_query.from_user.id, client_id
        )
        if not key or key["is_frozen"]:
            await callback_query.message.edit_text("❌ Выбранная подписка не найдена или заморожена.")
            await state.clear()
            return

        now_ms = int(datetime.now().timestamp() * 1000)
        current_expiry = key["expiry_time"]
        new_expiry = max(now_ms, current_expiry) + (coupon["days"] * 86400 * 1000)

        await renew_key_in_cluster(
            cluster_id=key["server_id"], email=key["email"], client_id=client_id, new_expiry_time=new_expiry, total_gb=0
        )
        await update_key_expiry(client_id, new_expiry, session)

        await update_coupon_usage_count(coupon["id"], session)
        await create_coupon_usage(coupon["id"], callback_query.from_user.id, session)

        alias = key.get("alias") or key["email"]
        expiry_date = datetime.fromtimestamp(new_expiry / 1000, tz=pytz.timezone("Europe/Moscow")).strftime(
            "%d.%m.%y, %H:%M"
        )
        await callback_query.message.answer(
            f"✅ Купон активирован, подписка <b>{alias}</b> продлена на {format_days(coupon['days'])}⏳ до {expiry_date}📆."
        )
        await process_callback_view_profile(callback_query.message, state, admin)
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при продлении ключа: {e}")
        await callback_query.message.edit_text("❌ Ошибка при активации купона.")
        await state.clear()


@router.callback_query(F.data == "cancel_coupon_activation")
async def cancel_coupon_activation(callback_query: CallbackQuery, state: FSMContext, admin: bool = False):
    await callback_query.message.edit_text("⚠️ Активация купона отменена.")
    await process_callback_view_profile(callback_query.message, state, admin)
    await state.clear()


@router.callback_query(F.data == "exit_coupon_input")
async def handle_exit_coupon_input(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    is_admin = callback_query.from_user.id in ADMIN_ID
    await process_callback_view_profile(callback_query.message, state, admin=is_admin)
