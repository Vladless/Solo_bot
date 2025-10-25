import html

from datetime import datetime
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import ADMIN_ID
from database import (
    add_payment,
    add_user,
    check_coupon_usage,
    check_user_exists,
    create_coupon_usage,
    get_coupon_by_code,
    get_keys,
    get_tariff_by_id,
    update_balance,
    update_coupon_usage_count,
    update_key_expiry,
)
from handlers.buttons import MAIN_MENU
from handlers.keys.operations import renew_key_in_cluster
from handlers.payments.currency_rates import format_for_user
from handlers.profile import process_callback_view_profile
from handlers.texts import (
    COUPONS_DAYS_MESSAGE,
    COUPON_ALREADY_USED_MSG,
    COUPON_DAYS_ACTIVATED_MSG,
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


@router.message(CouponActivationState.waiting_for_coupon_code, F.text)
async def process_coupon_code(message: Message, state: FSMContext, session: Any):
    coupon_code = message.text.strip()
    await activate_coupon(message, state, session, coupon_code=coupon_code)


async def activate_coupon(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    coupon_code: str,
    admin: bool = False,
    user_data: dict | None = None,
):
    logger.info(f"Активация купона: {coupon_code}")
    coupon = await get_coupon_by_code(session, coupon_code)

    if not coupon:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="exit_coupon_input"))
        await message.answer(COUPON_NOT_FOUND_MSG, reply_markup=builder.as_markup())
        return

    if coupon.usage_count >= coupon.usage_limit or coupon.is_used:
        await message.answer("❌ Лимит активаций купона исчерпан.")
        await state.clear()
        return

    user = user_data or message.from_user or message.chat
    language_code = user.get("language_code") if isinstance(user, dict) else getattr(user, "language_code", None)
    user_id = user["tg_id"] if isinstance(user, dict) else user.id

    usage = await check_coupon_usage(session, coupon.id, user_id)
    if usage:
        await message.answer(COUPON_ALREADY_USED_MSG)
        await state.clear()
        return

    user_exists = await check_user_exists(session, user_id)
    if not user_exists:
        if isinstance(user, dict):
            await add_user(session=session, **user)
        else:
            await add_user(
                session=session,
                tg_id=user.id,
                username=getattr(user, "username", None),
                first_name=getattr(user, "first_name", None),
                last_name=getattr(user, "last_name", None),
                language_code=getattr(user, "language_code", None),
                is_bot=getattr(user, "is_bot", False),
            )

    if coupon.amount > 0:
        try:
            await update_balance(session, user_id, coupon.amount)
            await update_coupon_usage_count(session, coupon.id)
            await create_coupon_usage(session, coupon.id, user_id)
            await add_payment(session, tg_id=user_id, amount=coupon.amount, payment_system="coupon")
            amount_txt = await format_for_user(session, user_id, coupon.amount, language_code)
            await message.answer(f"✅ Купон активирован, на баланс начислено {amount_txt}.")
            await state.clear()
        except Exception as e:
            logger.error(f"Ошибка при активации купона на баланс: {e}")
            await message.answer("❌ Ошибка при активации купона.")
            await state.clear()
        return

    if coupon.days:
        try:
            keys = await get_keys(session, user_id)
            active_keys = [k for k in keys if not k.is_frozen]

            if not active_keys:
                await message.answer("❌ У вас нет активных подписок для продления.")
                await state.clear()
                return

            builder = InlineKeyboardBuilder()
            moscow_tz = pytz.timezone("Europe/Moscow")
            response_message = COUPONS_DAYS_MESSAGE

            for key in active_keys:
                key_display = html.escape((key.alias or key.email).strip())
                expiry_date = datetime.fromtimestamp(key.expiry_time / 1000, tz=moscow_tz).strftime(
                    "до %d.%m.%y, %H:%M"
                )
                response_message += f"• <b>{key_display}</b> ({expiry_date})\n"
                builder.button(
                    text=key_display,
                    callback_data=f"extend_key|{key.client_id}|{coupon.id}",
                )

            response_message += "</blockquote>"
            builder.button(text="Отмена", callback_data="cancel_coupon_activation")
            builder.adjust(1)

            await message.answer(response_message, reply_markup=builder.as_markup())
            await state.set_state(CouponActivationState.waiting_for_key_selection)
            await state.update_data(coupon_id=coupon.id, user_id=user_id)
        except Exception as e:
            logger.error(f"Ошибка при обработке купона на дни: {e}")
            await message.answer("❌ Ошибка при активации купона.")
            await state.clear()
        return

    await message.answer("❌ Купон недействителен (нет суммы или дней).")
    await state.clear()


@router.callback_query(F.data.startswith("extend_key|"))
async def handle_key_extension(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    admin: bool = False,
):
    from database.models import Coupon, Key

    parts = callback_query.data.split("|")
    client_id = parts[1]
    coupon_id = int(parts[2])
    tg_id = callback_query.from_user.id

    try:
        result = await session.execute(select(Coupon).where(Coupon.id == coupon_id))
        coupon = result.scalar_one_or_none()
        if not coupon or coupon.usage_count >= coupon.usage_limit:
            await callback_query.message.edit_text("❌ Купон недействителен или лимит исчерпан.")
            await state.clear()
            return

        usage = await check_coupon_usage(session, coupon.id, tg_id)
        if usage:
            await callback_query.message.edit_text("❌ Вы уже активировали этот купон.")
            await state.clear()
            return

        result = await session.execute(select(Key).where(Key.tg_id == tg_id, Key.client_id == client_id))
        key = result.scalar_one_or_none()
        if not key or key.is_frozen:
            await callback_query.message.edit_text("❌ Выбранная подписка не найдена или заморожена.")
            await state.clear()
            return

        now_ms = int(datetime.now().timestamp() * 1000)
        current_expiry = key.expiry_time
        new_expiry = max(now_ms, current_expiry) + (coupon.days * 86400 * 1000)

        tariff = None
        if key.tariff_id:
            tariff = await get_tariff_by_id(session, key.tariff_id)
        total_gb = int(tariff["traffic_limit"]) if tariff and tariff.get("traffic_limit") else 0
        device_limit = int(tariff["device_limit"]) if tariff and tariff.get("device_limit") else 0

        key_subgroup = None
        if tariff:
            key_subgroup = tariff.get("subgroup_title")

        await renew_key_in_cluster(
            cluster_id=key.server_id,
            email=key.email,
            client_id=client_id,
            new_expiry_time=new_expiry,
            total_gb=total_gb,
            session=session,
            hwid_device_limit=device_limit,
            reset_traffic=False,
            target_subgroup=key_subgroup,
            old_subgroup=key_subgroup,
        )
        await update_key_expiry(session, client_id, new_expiry)
        await update_coupon_usage_count(session, coupon.id)
        await create_coupon_usage(session, coupon.id, tg_id)

        alias = key.alias or key.email
        expiry_date = datetime.fromtimestamp(new_expiry / 1000, tz=pytz.timezone("Europe/Moscow")).strftime(
            "%d.%m.%y, %H:%M"
        )
        await callback_query.message.answer(
            COUPON_DAYS_ACTIVATED_MSG.format(alias=alias, days=format_days(coupon.days), expiry=expiry_date)
        )
        await process_callback_view_profile(callback_query.message, state, admin, session)
        await state.clear()
    except Exception as e:
        logger.error(f"Ошибка при продлении ключа: {e}")
        await callback_query.message.edit_text("❌ Ошибка при активации купона.")
        await state.clear()


@router.callback_query(F.data == "cancel_coupon_activation")
async def cancel_coupon_activation(
    callback_query: CallbackQuery,
    state: FSMContext,
    admin: bool = False,
    session: AsyncSession = None,
):
    await callback_query.message.edit_text("⚠️ Активация купона отменена.")
    await process_callback_view_profile(callback_query.message, state, admin, session)
    await state.clear()


@router.callback_query(F.data == "exit_coupon_input")
async def handle_exit_coupon_input(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
):
    await state.clear()
    is_admin = callback_query.from_user.id in ADMIN_ID
    await process_callback_view_profile(callback_query.message, state, admin=is_admin, session=session)
