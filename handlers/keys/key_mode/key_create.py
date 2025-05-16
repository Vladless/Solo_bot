from datetime import datetime, timedelta
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    NOTIFY_EXTRA_DAYS,
    TRIAL_TIME,
    TRIAL_TIME_DISABLE,
    USE_COUNTRY_SELECTION,
    USE_NEW_PAYMENT_FLOW,
)
from database import add_user, check_user_exists, create_temporary_data, get_balance, get_tariffs_for_cluster, get_trial
from handlers.buttons import (
    MAIN_MENU,
    PAYMENT,
)
from handlers.payments.robokassa_pay import handle_custom_amount_input
from handlers.payments.yookassa_pay import process_custom_amount_input
from handlers.texts import (
    CREATING_CONNECTION_MSG,
    INSUFFICIENT_FUNDS_MSG,
    SELECT_TARIFF_PLAN_MSG,
)
from handlers.utils import edit_or_send_message, get_least_loaded_cluster
from logger import logger

from .key_cluster_mode import key_cluster_mode
from .key_country_mode import key_country_mode


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

    cluster_name = await get_least_loaded_cluster()
    tariffs = await get_tariffs_for_cluster(session, cluster_name)

    if not tariffs:
        await edit_or_send_message(
            target_message=message_or_query if isinstance(message_or_query, Message) else message_or_query.message,
            text="❌ Нет доступных тарифов для выбранного кластера.",
            reply_markup=None,
        )
        return

    builder = InlineKeyboardBuilder()
    for t in tariffs:
        builder.row(
            InlineKeyboardButton(
                text=f"{t['name']} — {t['price_rub']}₽",
                callback_data=f"select_tariff_plan|{t['id']}",
            )
        )
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    target_message = message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query
    await edit_or_send_message(
        target_message=target_message,
        text=SELECT_TARIFF_PLAN_MSG,
        reply_markup=builder.as_markup(),
    )

    await state.update_data(tg_id=tg_id)
    await state.set_state(Form.waiting_for_server_selection)


@router.callback_query(F.data.startswith("select_tariff_plan|"))
async def select_tariff_plan(callback_query: CallbackQuery, session: Any, state: FSMContext):
    tg_id = callback_query.message.chat.id
    tariff_id = int(callback_query.data.split("|")[1])

    row = await session.fetchrow("SELECT * FROM tariffs WHERE id = $1", tariff_id)
    if not row:
        await callback_query.message.edit_text("❌ Указанный тариф не найден.")
        return

    tariff = dict(row)
    duration_days = tariff["duration_days"]
    price_rub = tariff["price_rub"]

    balance = await get_balance(tg_id)
    if balance < price_rub:
        required_amount = price_rub - balance
        await create_temporary_data(
            session,
            tg_id,
            "waiting_for_payment",
            {
                "tariff_id": tariff_id,
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
    await state.update_data(tariff_id=tariff_id)
    await create_key(tg_id, expiry_time, state, session, callback_query, plan=tariff_id)


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
    if not await check_user_exists(tg_id):
        from_user = message_or_query.from_user if isinstance(message_or_query, CallbackQuery | Message) else None
        if from_user:
            await add_user(
                tg_id=from_user.id,
                username=from_user.username,
                first_name=from_user.first_name,
                last_name=from_user.last_name,
                language_code=from_user.language_code,
                is_bot=from_user.is_bot,
                session=session,
            )
            logger.info(f"[User] Новый пользователь {tg_id} добавлен")

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
