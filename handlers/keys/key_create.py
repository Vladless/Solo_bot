import os

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG, NOTIFICATIONS_CONFIG
from database import (
    add_user,
    get_tariffs_for_cluster,
    get_trial,
)
from database.access.resolution import notify_telegram_chat_id
from database.models import Admin
from database.notifications import check_cold_lead_discount, check_hot_lead_discount
from database.tariffs import create_subgroup_hash, find_subgroup_by_hash, get_subgroup_description, get_tariffs
from database.users import get_balance
from handlers.admin.panel.keyboard import AdminPanelCallback
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.utils import edit_or_send_message, format_discount_time_left, get_least_loaded_cluster
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import (
    process_purchase_tariff_group_override,
    process_tariff_menu,
)
from logger import logger
from services.tariffs.visibility import filter_visible_tariffs
from settings.buttons import BACK, MAIN_MENU
from settings.config import (
    DISCOUNT_ACTIVE_HOURS,
    NOTIFY_EXTRA_DAYS,
    TRIAL_TIME_DISABLE,
    USE_COUNTRY_SELECTION,
)
from settings.texts import (
    CREATING_CONNECTION_MSG,
    DISCOUNT_OFFER_MESSAGE,
    DISCOUNT_OFFER_STEP2,
    DISCOUNT_OFFER_STEP3,
    SELECT_TARIFF_PLAN_MSG,
)

from .key_mode.key_cluster_mode import key_cluster_mode
from .key_mode.key_country_mode import key_country_mode
from .utils import (
    add_tariff_button_generic,
    format_subgroup_description,
    format_tariff_descriptions,
    order_tariff_items,
)


router = Router()
moscow_tz = pytz.timezone("Europe/Moscow")


def _make_state_safe(data: dict) -> dict:
    safe = dict(data)
    for key, value in safe.items():
        if isinstance(value, datetime):
            safe[key] = value.isoformat()
    return safe


class Form(FSMContext):
    waiting_for_server_selection = "waiting_for_server_selection"


@router.callback_query(F.data == "create_key")
@router.callback_query(F.data == "buy")
@router.message(F.text == "/buy")
async def confirm_create_new_key(
    callback_query_or_message: CallbackQuery | Message,
    state: FSMContext,
    session: AsyncSession,
):
    if isinstance(callback_query_or_message, CallbackQuery):
        tg_id = callback_query_or_message.from_user.id
        message_or_query: Message | CallbackQuery = callback_query_or_message
    else:
        tg_id = callback_query_or_message.from_user.id
        message_or_query = callback_query_or_message

    await handle_key_creation(tg_id, state, session, message_or_query)


async def handle_key_creation(
    tg_id: int,
    state: FSMContext,
    session: AsyncSession,
    message_or_query: Message | CallbackQuery,
):
    state_data = await state.get_data()
    if state_data.get("key_creation_in_progress"):
        logger.warning(f"[AntiSpam] Пользователь {tg_id} повторно нажал на покупку — игнор.")
        return

    await state.update_data(key_creation_in_progress=True)

    target_message = message_or_query.message if isinstance(message_or_query, CallbackQuery) else message_or_query
    language_code = message_or_query.from_user.language_code

    try:
        current_time = datetime.now(moscow_tz)

        trial_time_disabled = bool(MODES_CONFIG.get("TRIAL_TIME_DISABLED", TRIAL_TIME_DISABLE))
        if not trial_time_disabled:
            trial_status = await get_trial(session, tg_id)
            if trial_status in [0, -1]:
                trial_tariffs = await get_tariffs(session, group_code="trial")
                if not trial_tariffs:
                    builder = InlineKeyboardBuilder()
                    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                    await edit_or_send_message(
                        target_message=target_message,
                        text="❌ Пробная подписка временно недоступна.",
                        reply_markup=builder.as_markup(),
                    )
                    return

                trial_tariff = trial_tariffs[0]
                trial_price = float(trial_tariff.get("price_rub", 0) or 0)
                base_days = trial_tariff["duration_days"]
                extra_days_value = int(NOTIFICATIONS_CONFIG.get("EXTRA_DAYS_AFTER_EXPIRY", NOTIFY_EXTRA_DAYS))
                extra_days = extra_days_value if trial_status == -1 else 0
                total_days = base_days + extra_days
                expiry_time = current_time + timedelta(days=total_days)

                if trial_price > 0:
                    balance = await get_balance(session, tg_id)
                    if balance < trial_price:
                        shortfall = int(trial_price - balance)
                        handled = await try_fast_payment_flow(
                            message_or_query if isinstance(message_or_query, CallbackQuery) else None,
                            session,
                            state,
                            tg_id=tg_id,
                            temp_key="waiting_for_payment",
                            temp_payload={
                                "payment_flow": "trial_purchase",
                                "tariff_id": trial_tariff["id"],
                                "selected_price_rub": int(trial_price),
                                "selected_duration_days": total_days,
                            },
                            required_amount=shortfall,
                        )
                        if handled:
                            return
                        builder = InlineKeyboardBuilder()
                        builder.row(InlineKeyboardButton(text="💳 Пополнить баланс", callback_data="pay"))
                        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                        await edit_or_send_message(
                            target_message=target_message,
                            text=f"💰 Пробная подписка стоит {int(trial_price)} ₽.\n"
                            f"Ваш баланс: {int(balance)} ₽.\n\n"
                            f"Пополните баланс для активации.",
                            reply_markup=builder.as_markup(),
                        )
                        return

                logger.info(
                    f"[Trial] Доступен {total_days}-дневный триал для пользователя {tg_id} (цена: {trial_price})"
                )

                await edit_or_send_message(
                    target_message=target_message,
                    text=CREATING_CONNECTION_MSG,
                    reply_markup=None,
                )

                await state.update_data(is_trial=True, plan=trial_tariff["id"])
                await create_key(
                    tg_id=tg_id,
                    expiry_time=expiry_time,
                    state=state,
                    session=session,
                    message_or_query=message_or_query,
                    old_key_name=None,
                    plan=trial_tariff["id"],
                )
                return

        try:
            cluster_name = await get_least_loaded_cluster(session)
        except ValueError as e:
            logger.error(f"Нет доступных кластеров: {e}")
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            await edit_or_send_message(
                target_message=target_message,
                text=str(e),
                reply_markup=builder.as_markup(),
            )
            return

        tariffs = await get_tariffs_for_cluster(session, cluster_name)

        discount_info: dict[str, Any] | None = None

        if tariffs:
            group_code = tariffs[0].get("group_code")
            original_group_code = group_code
            if group_code:
                discount_info = await check_hot_lead_discount(session, tg_id)
                if not (discount_info and discount_info.get("available")):
                    cold_discount_info = await check_cold_lead_discount(session, tg_id)
                    if cold_discount_info and cold_discount_info.get("available"):
                        discount_info = cold_discount_info

                if discount_info and discount_info.get("available"):
                    group_code = discount_info["tariff_group"]
                    await state.update_data(discount_info=_make_state_safe(discount_info))
                else:
                    await state.update_data(discount_info=None)

                override_result = await process_purchase_tariff_group_override(
                    chat_id=tg_id,
                    admin=False,
                    session=session,
                    original_group=group_code,
                )
                if override_result:
                    group_code = override_result["override_group"]
                    logger.info(f"[PURCHASE] Тарифная группа переопределена хуком: {group_code}")
                    if override_result.get("discount_info"):
                        await state.update_data(discount_info=_make_state_safe(override_result["discount_info"]))

                tariffs_data = await get_tariffs(
                    session,
                    group_code=group_code,
                    with_subgroup_weights=True,
                )
                tariffs = await filter_visible_tariffs(
                    session, tg_id, [t for t in tariffs_data["tariffs"] if t.get("is_active")]
                )
                tariffs_data["subgroup_weights"]

                if not tariffs and discount_info and discount_info.get("available"):
                    logger.warning(f"[PURCHASE] Нет тарифов со скидкой {group_code}, fallback на {original_group_code}")
                    group_code = original_group_code
                    tariffs_data = await get_tariffs(
                        session,
                        group_code=group_code,
                        with_subgroup_weights=True,
                    )
                    tariffs = await filter_visible_tariffs(
                        session, tg_id, [t for t in tariffs_data["tariffs"] if t.get("is_active")]
                    )
                    tariffs_data["subgroup_weights"]
                    discount_info = None
                    await state.update_data(discount_info=None)

        if not tariffs:
            result = await session.execute(select(Admin).where(Admin.tg_id == tg_id))
            is_admin = result.scalar_one_or_none() is not None

            if is_admin:
                builder = InlineKeyboardBuilder()
                builder.row(
                    InlineKeyboardButton(
                        text="🔗 Привязать тариф",
                        callback_data=AdminPanelCallback(action="clusters").pack(),
                    )
                )
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

                text = (
                    "🚫 <b>Невозможно создать подписку</b>\n\n"
                    "📊 <b>Информация о кластере:</b>\n<blockquote>"
                    f"🌐 <b>Кластер:</b> <code>{cluster_name}</code>\n"
                    "⚠️ <b>Статус:</b> Нет привязанного тарифа\n</blockquote>"
                    "💡 <b>Привяжите тариф к кластеру</b>"
                )
            else:
                builder = InlineKeyboardBuilder()
                builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
                text = "❌ Нет доступных тарифов для выбора."

            await edit_or_send_message(
                target_message=target_message,
                text=text,
                reply_markup=builder.as_markup(),
            )
            return

        group_code = tariffs[0].get("group_code") if tariffs else None
        if not group_code:
            await edit_or_send_message(
                target_message=target_message,
                text="❌ Не удалось определить группу тарифов.",
                reply_markup=None,
            )
            return

        grouped_tariffs: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for tariff in tariffs:
            subgroup = tariff.get("subgroup_title")
            grouped_tariffs[subgroup].append(tariff)

        builder = InlineKeyboardBuilder()

        for kind, payload in order_tariff_items(grouped_tariffs):
            if kind == "tariff":
                await add_tariff_button_generic(
                    builder=builder,
                    tariff=payload,
                    session=session,
                    tg_id=tg_id,
                    language_code=language_code,
                    callback_prefix="select_tariff_plan",
                )
            else:
                subgroup_hash = create_subgroup_hash(payload, group_code)
                builder.row(
                    InlineKeyboardButton(
                        text=payload,
                        callback_data=f"tariff_subgroup_user|{subgroup_hash}",
                    )
                )

        tariff_menu_buttons = await process_tariff_menu(
            group_code=group_code,
            cluster_name=cluster_name,
            tg_id=tg_id,
            session=session,
        )
        builder = insert_hook_buttons(builder, tariff_menu_buttons)

        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

        discount_message = ""
        if discount_info and discount_info.get("available"):
            offer_text = DISCOUNT_OFFER_STEP2 if discount_info["type"] == "hot_lead_step_2" else DISCOUNT_OFFER_STEP3
            expires_at = discount_info["expires_at"]
            discount_active_hours = int(NOTIFICATIONS_CONFIG.get("DISCOUNT_ACTIVE_HOURS", DISCOUNT_ACTIVE_HOURS))
            time_left = format_discount_time_left(
                expires_at - timedelta(hours=discount_active_hours),
                discount_active_hours,
            )
            discount_message = DISCOUNT_OFFER_MESSAGE.format(offer_text=offer_text, time_left=time_left)

        await edit_or_send_message(
            target_message=target_message,
            text=SELECT_TARIFF_PLAN_MSG + format_tariff_descriptions(grouped_tariffs.get(None, [])) + discount_message,
            reply_markup=builder.as_markup(),
            media_path=os.path.join("img", "tariffs.jpg"),
            disable_web_page_preview=False,
            force_text=True,
        )

        await state.update_data(
            tg_id=tg_id,
            cluster_name=cluster_name,
            group_code=group_code,
            tariff_subgroup_hash=None,
        )
        await state.set_state(Form.waiting_for_server_selection)

    finally:
        await state.update_data(key_creation_in_progress=False)


@router.callback_query(F.data.startswith("tariff_subgroup_user|"))
async def show_tariffs_in_subgroup_user(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
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

    await state.update_data(tariff_subgroup_hash=subgroup_hash)

    tariffs_for_cluster = await get_tariffs_for_cluster(session, cluster_name)
    filtered: list[dict[str, Any]] = []

    if tariffs_for_cluster:
        group_code = tariffs_for_cluster[0].get("group_code")
        if group_code:
            tariffs = await get_tariffs(session, group_code=group_code)
            filtered = await filter_visible_tariffs(
                session,
                callback.from_user.id,
                [tariff for tariff in tariffs if tariff.get("subgroup_title") == subgroup and tariff.get("is_active")],
            )

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
    for tariff in filtered:
        await add_tariff_button_generic(
            builder=builder,
            tariff=tariff,
            session=session,
            tg_id=tg_id,
            language_code=language_code,
            callback_prefix="select_tariff_plan",
        )

    builder.row(InlineKeyboardButton(text=BACK, callback_data="back_to_tariff_group_list"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    sub_desc = await get_subgroup_description(session, group_code, subgroup)
    await edit_or_send_message(
        target_message=callback.message,
        text=f"<b>{subgroup}</b>\n\n"
        + format_subgroup_description(sub_desc)
        + "Выберите тариф:"
        + format_tariff_descriptions(filtered),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "back_to_tariff_group_list")
async def back_to_tariff_group_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tg_id = callback.from_user.id
    await handle_key_creation(
        tg_id=tg_id,
        state=state,
        session=session,
        message_or_query=callback,
    )


@router.callback_query(F.data == "back_to_subgroup_tariffs")
async def back_to_subgroup_tariffs(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    """Возврат к списку тарифов текущей подгруппы (из конфигуратора)."""
    data = await state.get_data()
    subgroup_hash = data.get("tariff_subgroup_hash")
    if not subgroup_hash:
        await back_to_tariff_group_list(callback, state, session)
        return
    cluster_name = data.get("cluster_name")
    group_code = data.get("group_code")

    subgroup = await find_subgroup_by_hash(session, subgroup_hash, group_code)
    if not subgroup:
        await back_to_tariff_group_list(callback, state, session)
        return

    tariffs_for_cluster = await get_tariffs_for_cluster(session, cluster_name)
    filtered: list[dict[str, Any]] = []
    if tariffs_for_cluster:
        gc = tariffs_for_cluster[0].get("group_code")
        if gc:
            tariffs = await get_tariffs(session, group_code=gc)
            filtered = await filter_visible_tariffs(
                session,
                callback.from_user.id,
                [t for t in tariffs if t.get("subgroup_title") == subgroup and t.get("is_active")],
            )

    if not filtered:
        await back_to_tariff_group_list(callback, state, session)
        return

    tg_id = callback.from_user.id
    language_code = getattr(callback.from_user, "language_code", None)
    builder = InlineKeyboardBuilder()
    for tariff in filtered:
        await add_tariff_button_generic(
            builder=builder,
            tariff=tariff,
            session=session,
            tg_id=tg_id,
            language_code=language_code,
            callback_prefix="select_tariff_plan",
        )
    builder.row(InlineKeyboardButton(text=BACK, callback_data="back_to_tariff_group_list"))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    sub_desc = await get_subgroup_description(session, group_code, subgroup)
    await edit_or_send_message(
        target_message=callback.message,
        text=f"<b>{subgroup}</b>\n\n"
        + format_subgroup_description(sub_desc)
        + "Выберите тариф:"
        + format_tariff_descriptions(filtered),
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


async def create_key(
    tg_id: int,
    expiry_time: datetime,
    state: FSMContext | None,
    session: AsyncSession,
    message_or_query: Message | CallbackQuery | None = None,
    old_key_name: str | None = None,
    plan: int | None = None,
    selected_duration_days: int | None = None,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
    selected_price_rub: int | None = None,
    skip_balance_charge: bool | None = None,
    is_trial: bool = False,
):
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

    country_cfg = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))
    if country_cfg:
        tg_notify = await notify_telegram_chat_id(session, tg_id)
        can_deliver_country_ui = message_or_query is not None or tg_notify is not None
        use_country_selection = can_deliver_country_ui
    else:
        use_country_selection = False

    if state and (
        is_trial
        or skip_balance_charge is not None
        or any(
            value is not None
            for value in (selected_duration_days, selected_device_limit, selected_traffic_gb, selected_price_rub)
        )
    ):
        state_data = await state.get_data()
        if is_trial:
            state_data["is_trial"] = True
        if selected_duration_days is not None:
            state_data["config_selected_duration_days"] = selected_duration_days
        if selected_device_limit is not None:
            state_data["config_selected_device_limit"] = selected_device_limit
        if selected_traffic_gb is not None:
            state_data["config_selected_traffic_gb"] = selected_traffic_gb
        if selected_price_rub is not None:
            state_data["config_selected_price_rub"] = selected_price_rub
        if skip_balance_charge is not None:
            state_data["skip_balance_charge"] = skip_balance_charge
        await state.set_data(state_data)

    if use_country_selection:
        await key_country_mode(
            tg_id=tg_id,
            expiry_time=expiry_time,
            state=state,
            session=session,
            message_or_query=message_or_query,
            old_key_name=old_key_name,
            plan=plan,
            selected_device_limit=selected_device_limit,
            selected_traffic_gb=selected_traffic_gb,
            selected_price_rub=selected_price_rub,
            skip_balance_charge=skip_balance_charge,
        )
    else:
        await key_cluster_mode(
            tg_id=tg_id,
            expiry_time=expiry_time,
            state=state,
            session=session,
            message_or_query=message_or_query,
            plan=plan,
            selected_device_limit=selected_device_limit,
            selected_traffic_gb=selected_traffic_gb,
            selected_price_rub=selected_price_rub,
            skip_balance_charge=skip_balance_charge,
            is_trial=is_trial or None,
        )
