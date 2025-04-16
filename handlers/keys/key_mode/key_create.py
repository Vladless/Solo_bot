from datetime import datetime, timedelta
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from .key_cluster_mode import key_cluster_mode
from .key_country_mode import key_country_mode

from config import (
    NOTIFY_EXTRA_DAYS,
    RENEWAL_PRICES,
    TRIAL_TIME,
    TRIAL_TIME_DISABLE,
    USE_COUNTRY_SELECTION,
    USE_NEW_PAYMENT_FLOW,
)
from database import (
    add_connection,
    check_connection_exists,
    create_temporary_data,
    get_balance,
    get_trial,
)
from handlers.buttons import (
    MAIN_MENU,
    PAYMENT,
)
from handlers.payments.robokassa_pay import handle_custom_amount_input
from handlers.payments.yookassa_pay import process_custom_amount_input
from handlers.texts import (
    CREATING_CONNECTION_MSG,
    DISCOUNTS,
    INSUFFICIENT_FUNDS_MSG,
    SELECT_TARIFF_PLAN_MSG,
)
from handlers.utils import edit_or_send_message
from logger import logger


router = Router()

moscow_tz = pytz.timezone("Europe/Moscow")


class Form(FSMContext):
    waiting_for_server_selection = "waiting_for_server_selection"


@router.callback_query(F.data == "create_key")
async def confirm_create_new_key(callback_query: CallbackQuery, state: FSMContext, session: Any):
    tg_id = callback_query.message.chat.id
    await handle_key_creation(tg_id, state, session, callback_query)


async def handle_key_creation(
    tg_id: int,
    state: FSMContext,
    session: Any,
    message_or_query: Message | CallbackQuery,
):
    """Создание ключа с учётом выбора тарифного плана."""
    current_time = datetime.now(moscow_tz)

    if not TRIAL_TIME_DISABLE:
        trial_status = await get_trial(tg_id, session)
        if trial_status in [0, -1]:
            extra_days = NOTIFY_EXTRA_DAYS if trial_status == -1 else 0
            expiry_time = current_time + timedelta(days=TRIAL_TIME + extra_days)
            logger.info(f"Доступен {TRIAL_TIME + extra_days}-дневный пробный период пользователю {tg_id}.")
            await edit_or_send_message(
                target_message=message_or_query if isinstance(message_or_query, Message) else message_or_query.message,
                text=CREATING_CONNECTION_MSG,
                reply_markup=None,
            )
            await state.update_data(is_trial=True)
            await create_key(tg_id, expiry_time, state, session, message_or_query)
            return

    builder = InlineKeyboardBuilder()
    for index, (plan_id, price) in enumerate(RENEWAL_PRICES.items()):
        discount_text = ""
        if DISCOUNTS and plan_id in DISCOUNTS:
            discount_percentage = DISCOUNTS[plan_id]
            discount_text = f" ({discount_percentage}% скидка)"
            if index == len(RENEWAL_PRICES) - 1:
                discount_text = f" ({discount_percentage}% 🔥)"
        builder.row(
            InlineKeyboardButton(
                text=f"📅 {plan_id} мес. - {price}₽{discount_text}",
                callback_data=f"select_plan_{plan_id}",
            )
        )
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    if isinstance(message_or_query, CallbackQuery):
        target_message = message_or_query.message
    else:
        target_message = message_or_query

    await edit_or_send_message(
        target_message=target_message,
        text=SELECT_TARIFF_PLAN_MSG,
        reply_markup=builder.as_markup(),
        media_path=None,
    )

    await state.update_data(tg_id=tg_id)
    await state.set_state(Form.waiting_for_server_selection)


@router.callback_query(F.data.startswith("select_plan_"))
async def select_tariff_plan(callback_query: CallbackQuery, session: Any, state: FSMContext):
    tg_id = callback_query.message.chat.id
    plan_id = callback_query.data.split("_")[-1]
    plan_price = RENEWAL_PRICES.get(plan_id)
    if plan_price is None:
        await callback_query.message.answer("🚫 Неверный тарифный план.")
        return
    duration_days = int(plan_id) * 30
    balance = await get_balance(tg_id)
    if balance < plan_price:
        required_amount = plan_price - balance
        await create_temporary_data(
            session,
            tg_id,
            "waiting_for_payment",
            {
                "plan_id": plan_id,
                "plan_price": plan_price,
                "duration_days": duration_days,
                "required_amount": required_amount,
            },
        )
        if USE_NEW_PAYMENT_FLOW == "YOOKASSA":
            await process_custom_amount_input(callback_query, session)
        elif USE_NEW_PAYMENT_FLOW == "ROBOKASSA":
            await handle_custom_amount_input(callback_query, session)
        else:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text=INSUFFICIENT_FUNDS_MSG.format(required_amount=required_amount),
                reply_markup=builder.as_markup(),
                media_path=None,
            )
        return
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⏳ Подождите...", callback_data="creating_key"))

    await edit_or_send_message(
        target_message=callback_query.message,
        text=CREATING_CONNECTION_MSG,
        reply_markup=builder.as_markup(),
    )

    expiry_time = datetime.now(moscow_tz) + timedelta(days=duration_days)
    await state.update_data(plan_id=plan_id)
    await create_key(tg_id, expiry_time, state, session, callback_query, plan=int(plan_id))


async def create_key(
    tg_id: int,
    expiry_time,
    state,
    session,
    message_or_query=None,
    old_key_name: str = None,
    plan: int = None,
):
    """
    Универсальная точка входа для создания ключа.
    Делегирует выполнение в зависимости от выбранного режима (страна или кластер).
    Также отвечает за первичное подключение пользователя.
    """
    if not await check_connection_exists(tg_id):
        await add_connection(tg_id, balance=0.0, trial=0, session=session)
        logger.info(f"[Connection] Подключение создано для пользователя {tg_id}")

    if USE_COUNTRY_SELECTION:
        await key_country_mode(
            tg_id=tg_id,
            expiry_time=expiry_time,
            state=state,
            session=session,
            message_or_query=message_or_query,
            old_key_name=old_key_name,
        )
    else:
        await key_cluster_mode(
            tg_id=tg_id,
            expiry_time=expiry_time,
            state=state,
            session=session,
            message_or_query=message_or_query,
            plan=plan,
        )