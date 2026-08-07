from datetime import datetime, timedelta
from math import ceil
from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_balance, get_tariff_by_id
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.utils import edit_or_send_message, safe_answer_callback
from logger import logger
from services.payments.currency_rates import format_for_user
from services.tariffs.pricing import calculate_config_price
from services.tariffs.tariff_display import GB
from settings.buttons import BACK, MAIN_MENU, PAYMENT
from settings.config import USE_NEW_PAYMENT_FLOW
from settings.texts import (
    CREATING_CONNECTION_MSG,
    INSUFFICIENT_FUNDS_MSG,
)


CREATING_KEY_BUTTON_TEXT = "⏳ Подождите..."


async def proceed_purchase_with_values(
    callback_query: CallbackQuery,
    session: Any,
    state: FSMContext,
    tariff: dict,
    duration_days: int,
    price_rub: int,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
):
    """Проверяет баланс и создаёт ключ по выбранной конфигурации."""
    from ...keys.create.flow import create_key, moscow_tz

    tg_id = callback_query.from_user.id

    logger.info(
        "[TARIFF_CFG] proceed_purchase_with_values: "
        f"tg_id={tg_id} tariff_id={tariff.get('id')} duration_days={duration_days} "
        f"price_rub={price_rub} selected_device_limit={selected_device_limit} "
        f"selected_traffic_gb={selected_traffic_gb}"
    )

    balance = await get_balance(session, tg_id)

    if balance < price_rub:
        required_amount = ceil(price_rub - balance)

        logger.info(
            f"[TARIFF_CFG] insufficient_balance: tg_id={tg_id} balance={balance} required_amount={required_amount}"
        )

        if USE_NEW_PAYMENT_FLOW:
            handled = await try_fast_payment_flow(
                callback_query,
                session,
                state,
                tg_id=tg_id,
                temp_key="waiting_for_payment",
                temp_payload={
                    "tariff_id": tariff["id"],
                    "selected_price_rub": price_rub,
                    "selected_duration_days": duration_days,
                    "selected_device_limit": selected_device_limit,
                    "selected_traffic_limit_gb": selected_traffic_gb,
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
        builder.row(InlineKeyboardButton(text=BACK, callback_data="back_to_tariff_group_list"))
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=INSUFFICIENT_FUNDS_MSG.format(required_amount=required_amount_text),
            reply_markup=builder.as_markup(),
        )
        return

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=CREATING_KEY_BUTTON_TEXT, callback_data="creating_key"))
    await edit_or_send_message(
        target_message=callback_query.message,
        text=CREATING_CONNECTION_MSG,
        reply_markup=builder.as_markup(),
    )
    await safe_answer_callback(callback_query)

    expiry_time = datetime.now(moscow_tz) + timedelta(days=duration_days)

    data_to_update: dict[str, Any] = {"tariff_id": tariff["id"], "selected_price_rub": price_rub}
    if selected_device_limit is not None:
        data_to_update["config_selected_device_limit"] = selected_device_limit
    if selected_traffic_gb is not None:
        data_to_update["config_selected_traffic_gb"] = selected_traffic_gb

    await state.update_data(**data_to_update)

    logger.info(f"[TARIFF_CFG] create_key: tg_id={tg_id} tariff_id={tariff.get('id')} expiry_time={expiry_time}")

    await create_key(
        tg_id=tg_id,
        expiry_time=expiry_time,
        state=state,
        session=session,
        message_or_query=callback_query,
        plan=tariff["id"],
        selected_duration_days=duration_days,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
        selected_price_rub=price_rub,
    )


async def finalize_config_and_purchase(callback_query: CallbackQuery, state: FSMContext, session: Any):
    """Фиксирует выбор пользователя и проводит оплату тарифа."""
    data = await state.get_data()
    tariff_id = data.get("config_tariff_id")
    cfg = data.get("tariff_config") or {}

    if tariff_id is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Подождите, создаём подписку…",
            reply_markup=None,
        )
        await state.clear()
        logger.warning("[TARIFF_CFG] finalize_config_and_purchase: config_tariff_id missing in state")
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Подождите, создаём подписку…",
            reply_markup=None,
        )
        await state.clear()
        logger.warning(f"[TARIFF_CFG] finalize_config_and_purchase tariff_not_found: tariff_id={tariff_id}")
        return

    duration_days = int(tariff.get("duration_days") or 30)

    raw_device_options = tariff.get("device_options")
    raw_traffic_options = tariff.get("traffic_options_gb")

    raw_device_options = raw_device_options if isinstance(raw_device_options, list) else []
    raw_traffic_options = raw_traffic_options if isinstance(raw_traffic_options, list) else []

    try:
        device_options = sorted(raw_device_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        device_options = raw_device_options

    try:
        traffic_options = sorted(raw_traffic_options, key=lambda v: (int(v) == 0, int(v)))
    except (TypeError, ValueError):
        traffic_options = raw_traffic_options

    device_int_options: list[int] = []
    for value in device_options:
        try:
            device_int_options.append(int(value))
        except (TypeError, ValueError):
            continue

    traffic_int_options: list[int] = []
    for value in traffic_options:
        try:
            traffic_int_options.append(int(value))
        except (TypeError, ValueError):
            continue

    has_device_choice = bool(device_int_options)
    has_traffic_choice = bool(traffic_int_options)

    selected_devices = data.get("config_selected_device_limit")
    selected_traffic_gb = data.get("config_selected_traffic_gb")

    base_device_limit = cfg.get("base_device_limit")
    if base_device_limit is None:
        base_device_limit = tariff.get("device_limit")
    if base_device_limit is None:
        positives = [v for v in device_int_options if v > 0]
        if positives:
            base_device_limit = min(positives)
        elif device_int_options:
            base_device_limit = device_int_options[0]
    base_device = int(base_device_limit) if base_device_limit is not None else None

    base_traffic_gb = None
    raw_base_traffic = tariff.get("traffic_limit")
    if raw_base_traffic:
        raw_base_traffic = int(raw_base_traffic)
        if raw_base_traffic >= GB:
            base_traffic_gb = int(raw_base_traffic / GB)
        else:
            base_traffic_gb = raw_base_traffic
    if base_traffic_gb is None:
        cfg_base_traffic = cfg.get("base_traffic_gb")
        if cfg_base_traffic is not None and int(cfg_base_traffic) > 0:
            base_traffic_gb = int(cfg_base_traffic)
    if base_traffic_gb is None:
        positives = [v for v in traffic_int_options if v > 0]
        if positives:
            base_traffic_gb = min(positives)
        elif traffic_int_options:
            base_traffic_gb = traffic_int_options[0]
    base_traffic_gb = int(base_traffic_gb) if base_traffic_gb is not None else None

    if has_device_choice:
        if selected_devices is None:
            if base_device is not None and base_device in device_int_options:
                selected_devices = base_device
            elif device_int_options:
                selected_devices = device_int_options[0]
    else:
        selected_devices = None

    if has_traffic_choice:
        if selected_traffic_gb is None:
            if base_traffic_gb is not None and base_traffic_gb in traffic_int_options:
                selected_traffic_gb = base_traffic_gb
            elif traffic_int_options:
                selected_traffic_gb = traffic_int_options[0]
    else:
        selected_traffic_gb = None

    final_price = calculate_config_price(
        tariff=tariff,
        selected_device_limit=int(selected_devices) if selected_devices is not None and has_device_choice else None,
        selected_traffic_gb=int(selected_traffic_gb)
        if selected_traffic_gb is not None and has_traffic_choice
        else None,
    )

    logger.info(
        "[TARIFF_CFG] finalize_config_and_purchase: "
        f"tg_id={callback_query.from_user.id} tariff_id={tariff_id} duration_days={duration_days} "
        f"selected_devices={selected_devices} selected_traffic_gb={selected_traffic_gb} "
        f"final_price={final_price}"
    )

    await proceed_purchase_with_values(
        callback_query=callback_query,
        session=session,
        state=state,
        tariff=tariff,
        duration_days=duration_days,
        price_rub=final_price,
        selected_device_limit=int(selected_devices) if selected_devices is not None and has_device_choice else None,
        selected_traffic_gb=int(selected_traffic_gb)
        if selected_traffic_gb is not None and has_traffic_choice
        else None,
    )
