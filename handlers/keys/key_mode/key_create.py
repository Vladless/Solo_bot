from collections import defaultdict
from datetime import datetime, timedelta
from math import ceil
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from config import (
    DISCOUNT_ACTIVE_HOURS,
    NOTIFY_EXTRA_DAYS,
    TRIAL_TIME_DISABLE,
    USE_COUNTRY_SELECTION,
    USE_NEW_PAYMENT_FLOW,
)
from database import (
    add_user,
    check_user_exists,
    get_balance,
    get_tariff_by_id,
    get_tariffs_for_cluster,
    get_trial,
)
from database.models import Admin
from database.notifications import check_hot_lead_discount
from database.tariffs import create_subgroup_hash, find_subgroup_by_hash, get_tariffs
from handlers.admin.panel.keyboard import AdminPanelCallback
from handlers.buttons import MAIN_MENU, PAYMENT
from handlers.payments.currency_rates import format_for_user
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.texts import (
    CREATING_CONNECTION_MSG,
    DISCOUNT_OFFER_MESSAGE,
    DISCOUNT_OFFER_STEP2,
    DISCOUNT_OFFER_STEP3,
    INSUFFICIENT_FUNDS_MSG,
    SELECT_TARIFF_PLAN_MSG,
)
from handlers.utils import edit_or_send_message, format_discount_time_left, get_least_loaded_cluster
from hooks.hook_buttons import insert_hook_buttons
from hooks.hooks import run_hooks
from logger import logger

from .key_cluster_mode import key_cluster_mode
from .key_country_mode import key_country_mode


router = Router()

moscow_tz = pytz.timezone("Europe/Moscow")


class Form(FSMContext):
    waiting_for_server_selection = "waiting_for_server_selection"


@router.callback_query(F.data == "create_key")
@router.callback_query(F.data == "buy")
@router.message(F.text == "/buy")
async def confirm_create_new_key(callback_query_or_message: CallbackQuery | Message, state: FSMContext, session: Any):
    if isinstance(callback_query_or_message, CallbackQuery):
        tg_id = callback_query_or_message.message.chat.id
        message_or_query = callback_query_or_message
    else:
        tg_id = callback_query_or_message.chat.id
        message_or_query = callback_query_or_message

    await handle_key_creation(tg_id, state, session, message_or_query)


async def handle_key_creation(
    tg_id: int,
    state: FSMContext,
    session: Any,
    message_or_query: Message | CallbackQuery,
):
    state_data = await state.get_data()
    if state_data.get("key_creation_in_progress"):
        logger.warning(f"[AntiSpam] Пользователь {tg_id} повторно нажал на покупку — игнор.")
        return

    await state.update_data(key_creation_in_progress=True)

    try:
        current_time = datetime.now(moscow_tz)

        if not TRIAL_TIME_DISABLE:
            trial_status = await get_trial(session, tg_id)
            if trial_status in [0, -1]:
                trial_tariffs = await get_tariffs(session, group_code="trial")
                if not trial_tariffs:
                    await edit_or_send_message(
                        target_message=(
                            message_or_query.message
                            if isinstance(message_or_query, CallbackQuery)
                            else message_or_query
                        ),
                        text="❌ Пробная подписка временно недоступна.",
                        reply_markup=InlineKeyboardBuilder()
                        .row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                        .as_markup(),
                    )
                    return

                trial_tariff = trial_tariffs[0]
                base_days = trial_tariff["duration_days"]
                extra_days = NOTIFY_EXTRA_DAYS if trial_status == -1 else 0
                total_days = base_days + extra_days
                expiry_time = current_time + timedelta(days=total_days)

                logger.info(f"[Trial] Доступен {total_days}-дневный триал для пользователя {tg_id}")

                await edit_or_send_message(
                    target_message=(
                        message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query
                    ),
                    text=CREATING_CONNECTION_MSG,
                    reply_markup=None,
                )

                await state.update_data(is_trial=True, plan=trial_tariff["id"])
                await create_key(tg_id, expiry_time, state, session, message_or_query, plan=trial_tariff["id"])
                return

        try:
            cluster_name = await get_least_loaded_cluster(session)
        except ValueError as e:
            logger.error(f"Нет доступных кластеров: {e}")
            await edit_or_send_message(
                target_message=(
                    message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query
                ),
                text=str(e),
                reply_markup=InlineKeyboardBuilder()
                .row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                .as_markup(),
            )
            return

        tariffs = await get_tariffs_for_cluster(session, cluster_name)

        language_code = (
            message_or_query.from_user.language_code
            if not isinstance(message_or_query, CallbackQuery)
            else message_or_query.from_user.language_code
        )

        discount_info = None
        subgroup_weights = {}

        if tariffs:
            group_code = tariffs[0].get("group_code")
            if group_code:
                from database.notifications import check_hot_lead_discount

                discount_info = await check_hot_lead_discount(session, tg_id)

                if discount_info and discount_info.get("available"):
                    group_code = discount_info["tariff_group"]
                    await state.update_data(discount_info=discount_info)
                else:
                    await state.update_data(discount_info=None)

                try:
                    hook_results = await run_hooks(
                        "purchase_tariff_group_override",
                        chat_id=tg_id,
                        admin=False,
                        session=session,
                        original_group=group_code,
                    )
                    for hook_result in hook_results:
                        if hook_result.get("override_group"):
                            group_code = hook_result["override_group"]
                            logger.info(f"[PURCHASE] Тарифная группа переопределена хуком: {group_code}")

                            if hook_result.get("discount_info"):
                                await state.update_data(discount_info=hook_result["discount_info"])
                            break
                except Exception as e:
                    logger.warning(f"[PURCHASE] Ошибка при применении хуков переопределения группы: {e}")

                tariffs_data = await get_tariffs(session, group_code=group_code, with_subgroup_weights=True)
                tariffs = [t for t in tariffs_data["tariffs"] if t.get("is_active")]
                subgroup_weights = tariffs_data["subgroup_weights"]

        if not tariffs:
            result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
            is_admin = result.scalar_one_or_none() is not None

            if is_admin:
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(
                        text="🔗 Привязать тариф", callback_data=AdminPanelCallback(action="clusters").pack()
                    )
                )
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

                text = (
                    f"🚫 <b>Невозможно создать подписку</b>\n\n"
                    f"📊 <b>Информация о кластере:</b>\n<blockquote>"
                    f"🌐 <b>Кластер:</b> <code>{cluster_name}</code>\n"
                    f"⚠️ <b>Статус:</b> Нет привязанного тарифа\n</blockquote>"
                    f"💡 <b>Привяжите тариф к кластеру</b>"
                )
            else:
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                text = "❌ Нет доступных тарифов для выбора."

            await edit_or_send_message(
                target_message=(
                    message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query
                ),
                text=text,
                reply_markup=builder.as_markup(),
            )
            return

        group_code = tariffs[0].get("group_code") if tariffs else None
        if not group_code:
            await edit_or_send_message(
                target_message=(
                    message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query
                ),
                text="❌ Не удалось определить группу тарифов.",
                reply_markup=None,
            )
            return

        grouped_tariffs = defaultdict(list)
        for t in tariffs:
            subgroup = t.get("subgroup_title")
            grouped_tariffs[subgroup].append(t)

        builder = InlineKeyboardBuilder()

        for t in grouped_tariffs.get(None, []):
            price_txt = await format_for_user(session, tg_id, t.get("price_rub", 0), language_code)
            builder.row(
                InlineKeyboardButton(
                    text=f"{t['name']} — {price_txt}",
                    callback_data=f"select_tariff_plan|{t['id']}",
                )
            )

        sorted_subgroups = sorted(
            [k for k in grouped_tariffs if k],
            key=lambda x: (subgroup_weights.get(x, 999999) if subgroup_weights else 999999, x),
        )

        for subgroup in sorted_subgroups:
            subgroup_hash = create_subgroup_hash(subgroup, group_code)
            builder.row(
                InlineKeyboardButton(
                    text=f"{subgroup}",
                    callback_data=f"tariff_subgroup_user|{subgroup_hash}",
                )
            )

        tariff_menu_buttons = await run_hooks(
            "tariff_menu", group_code=group_code, cluster_name=cluster_name, tg_id=tg_id, session=session
        )
        builder = insert_hook_buttons(builder, tariff_menu_buttons)

        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        target_message = message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query

        discount_message = ""

        if discount_info and discount_info.get("available"):
            offer_text = DISCOUNT_OFFER_STEP2 if discount_info["type"] == "hot_lead_step_2" else DISCOUNT_OFFER_STEP3
            expires_at = discount_info["expires_at"]
            time_left = format_discount_time_left(
                expires_at - timedelta(hours=DISCOUNT_ACTIVE_HOURS), DISCOUNT_ACTIVE_HOURS
            )
            discount_message = DISCOUNT_OFFER_MESSAGE.format(offer_text=offer_text, time_left=time_left)

        await edit_or_send_message(
            target_message=target_message,
            text=SELECT_TARIFF_PLAN_MSG + discount_message,
            reply_markup=builder.as_markup(),
        )

        await state.update_data(tg_id=tg_id, cluster_name=cluster_name, group_code=group_code)
        await state.set_state(Form.waiting_for_server_selection)

    finally:
        await state.update_data(key_creation_in_progress=False)


@router.callback_query(F.data.startswith("tariff_subgroup_user|"))
async def show_tariffs_in_subgroup_user(callback: CallbackQuery, state: FSMContext, session: Any):
    subgroup_hash = callback.data.split("|")[1]
    data = await state.get_data()
    cluster_name = data.get("cluster_name")
    group_code = data.get("group_code")

    subgroup = await find_subgroup_by_hash(session, subgroup_hash, group_code)
    if not subgroup:
        await edit_or_send_message(
            target_message=callback.message,
            text="❌ Подгруппа не найдена.",
            reply_markup=None,
        )
        return

    tariffs = await get_tariffs_for_cluster(session, cluster_name)
    filtered = []

    if tariffs:
        group_code = tariffs[0].get("group_code")
        if group_code:
            tariffs = await get_tariffs(session, group_code=group_code)
            filtered = [t for t in tariffs if t.get("subgroup_title") == subgroup and t.get("is_active")]

    if not filtered:
        await edit_or_send_message(
            target_message=callback.message,
            text="❌ В этой подгруппе пока нет тарифов.",
            reply_markup=None,
        )
        return

    tg_id = callback.from_user.id
    language_code = callback.from_user.language_code

    builder = InlineKeyboardBuilder()
    for t in filtered:
        price_txt = await format_for_user(session, tg_id, t.get("price_rub", 0), language_code)
        builder.row(
            InlineKeyboardButton(
                text=f"{t['name']} — {price_txt}",
                callback_data=f"select_tariff_plan|{t['id']}",
            )
        )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_tariff_group_list"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await edit_or_send_message(
        target_message=callback.message,
        text=f"<b>{subgroup}</b>\n\nВыберите тариф:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "back_to_tariff_group_list")
async def back_to_tariff_group_list(callback: CallbackQuery, state: FSMContext, session: Any):
    await state.get_data()
    tg_id = callback.from_user.id
    await handle_key_creation(
        tg_id=tg_id,
        state=state,
        session=session,
        message_or_query=callback,
    )


@router.callback_query(F.data.startswith("select_tariff_plan|"))
async def select_tariff_plan(callback_query: CallbackQuery, session: Any, state: FSMContext):
    tg_id = callback_query.from_user.id
    tariff_id = int(callback_query.data.split("|")[1])

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Указанный тариф не найден.",
        )
        await callback_query.answer()
        return

    discount_info = await check_hot_lead_discount(session, tg_id)
    if tariff.get("group_code") in ["discounts", "discounts_max"]:
        if not discount_info.get("available") or datetime.utcnow() >= discount_info["expires_at"]:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text="❌ Скидка недоступна или истекла. Пожалуйста, выберите тариф заново.",
                reply_markup=builder.as_markup(),
            )
            await callback_query.answer()
            return

    try:
        hook_results = await run_hooks(
            "check_discount_validity",
            chat_id=tg_id,
            admin=False,
            session=session,
            tariff_group=tariff.get("group_code"),
        )
        for hook_result in hook_results:
            if not hook_result.get("valid", True):
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                await edit_or_send_message(
                    target_message=callback_query.message,
                    text=hook_result.get("message", "❌ Скидка недоступна. Пожалуйста, выберите тариф заново."),
                    reply_markup=builder.as_markup(),
                )
                await callback_query.answer()
                return
    except Exception as e:
        logger.warning(f"[PURCHASE] Ошибка при проверке скидок через хуки: {e}")

    duration_days = tariff["duration_days"]
    price_rub = tariff["price_rub"]

    balance = await get_balance(session, tg_id)

    if balance < price_rub:
        required_amount = ceil(price_rub - balance)

        if USE_NEW_PAYMENT_FLOW:
            handled = await try_fast_payment_flow(
                callback_query,
                session,
                state,
                tg_id=tg_id,
                temp_key="waiting_for_payment",
                temp_payload={
                    "tariff_id": tariff_id,
                    "duration_days": duration_days,
                    "required_amount": required_amount,
                },
                required_amount=required_amount,
            )
            if handled:
                return

        language_code = getattr(callback_query.from_user, "language_code", None)
        required_amount_text = await format_for_user(session, tg_id, float(required_amount), language_code)

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=INSUFFICIENT_FUNDS_MSG.format(required_amount=required_amount_text),
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
    await callback_query.answer()

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
    if not await check_user_exists(session, tg_id):
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

    if USE_COUNTRY_SELECTION:
        await key_country_mode(
            tg_id=tg_id,
            expiry_time=expiry_time,
            state=state,
            session=session,
            message_or_query=message_or_query,
            old_key_name=old_key_name,
            plan=plan,
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
