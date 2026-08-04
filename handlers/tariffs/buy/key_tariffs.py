from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import MODES_CONFIG
from core.settings.tariffs_config import normalize_tariff_config
from database import get_balance, get_tariff_by_id
from database.notifications import check_cold_lead_discount, check_hot_lead_discount
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.utils import edit_or_send_message, get_plural_form, safe_answer_callback
from hooks.processors import process_check_discount_validity
from logger import logger
from services.payments.currency_rates import format_for_user
from services.tariffs.cooldown import format_cooldown_left, get_tariff_cooldown_remaining
from services.tariffs.tariff_display import GB
from settings.buttons import BACK, CONFIG_PAY_BUTTON_TEXT, MAIN_MENU, PAYMENT
from settings.config import USE_NEW_PAYMENT_FLOW
from settings.texts import (
    ADDON_RESET_CONFIG_WARNING,
    CONFIG_SCREEN_TEMPLATE,
    CREATING_CONNECTION_MSG,
    DEFAULT_LIMIT_LABEL,
    INSUFFICIENT_FUNDS_MSG,
    TARIFF_COOLDOWN_MESSAGE,
    UNLIMITED_DEVICES_LABEL,
    UNLIMITED_TRAFFIC_LABEL,
)


router = Router()


CREATING_KEY_BUTTON_TEXT = "⏳ Подождите..."


class TariffUserConfigState(StatesGroup):
    """Состояния конфигуратора тарифа для пользователя."""

    configuring = State()


def calculate_config_price(
    tariff: dict,
    selected_device_limit: int | None = None,
    selected_traffic_gb: int | None = None,
) -> int:
    """Рассчитывает цену тарифа с учётом выбранных лимитов."""
    cfg = normalize_tariff_config(tariff)

    base_price = int(tariff.get("price_rub") or 0)

    raw_device_options = tariff.get("device_options")
    raw_traffic_options = tariff.get("traffic_options_gb")

    raw_device_options = raw_device_options if isinstance(raw_device_options, list) else []
    raw_traffic_options = raw_traffic_options if isinstance(raw_traffic_options, list) else []

    device_values: list[int] = []
    for value in raw_device_options:
        try:
            device_values.append(int(value))
        except (TypeError, ValueError):
            continue

    traffic_values: list[int] = []
    for value in raw_traffic_options:
        try:
            traffic_values.append(int(value))
        except (TypeError, ValueError):
            continue

    positive_device_values = [v for v in device_values if v > 0]
    positive_traffic_values = [v for v in traffic_values if v > 0]

    base_device_limit = cfg.get("base_device_limit")
    if base_device_limit is None:
        base_device_limit = tariff.get("device_limit")
    if base_device_limit is None:
        if positive_device_values:
            base_device_limit = min(positive_device_values)
        elif device_values:
            base_device_limit = device_values[0]
    base_device_limit = int(base_device_limit) if base_device_limit is not None else None

    base_traffic_gb = cfg.get("base_traffic_gb")
    if base_traffic_gb is None:
        traffic_limit_raw = tariff.get("traffic_limit")
        if traffic_limit_raw:
            traffic_limit_raw = int(traffic_limit_raw)
            if traffic_limit_raw >= GB:
                base_traffic_gb = int(traffic_limit_raw / GB)
            else:
                base_traffic_gb = traffic_limit_raw
        else:
            if positive_traffic_values:
                base_traffic_gb = min(positive_traffic_values)
            elif traffic_values:
                base_traffic_gb = traffic_values[0]
    base_traffic_gb = int(base_traffic_gb) if base_traffic_gb is not None else None

    device_overrides = cfg.get("device_price_overrides") or tariff.get("device_overrides") or {}
    traffic_overrides = cfg.get("traffic_price_overrides") or tariff.get("traffic_overrides") or {}

    extra_device_step_price = int(cfg.get("extra_device_base_price_rub") or tariff.get("device_step_rub") or 0)
    extra_traffic_step_price = int(
        cfg.get("extra_traffic_base_price_per_gb_rub") or tariff.get("traffic_step_rub") or 0
    )

    devices_extra_price = 0
    traffic_extra_price = 0

    if selected_device_limit is not None and base_device_limit is not None:
        selected_device_limit = int(selected_device_limit)
        override_key = str(selected_device_limit)

        if override_key in device_overrides:
            devices_extra_price = int(device_overrides[override_key])
        else:
            if selected_device_limit <= 0:
                if positive_device_values:
                    effective_devices = max(positive_device_values)
                    extra_devices = max(0, effective_devices - base_device_limit)
                    devices_extra_price = extra_devices * extra_device_step_price
            else:
                extra_devices = max(0, selected_device_limit - base_device_limit)
                devices_extra_price = extra_devices * extra_device_step_price

    if selected_traffic_gb is not None and base_traffic_gb is not None:
        selected_traffic_gb = int(selected_traffic_gb)
        override_key = str(selected_traffic_gb)

        if override_key in traffic_overrides:
            traffic_extra_price = int(traffic_overrides[override_key])
        else:
            if selected_traffic_gb <= 0:
                if positive_traffic_values:
                    effective_gb = max(positive_traffic_values)
                    extra_traffic = max(0, effective_gb - base_traffic_gb)
                    traffic_extra_price = extra_traffic * extra_traffic_step_price
            else:
                extra_traffic = max(0, selected_traffic_gb - base_traffic_gb)
                traffic_extra_price = extra_traffic * extra_traffic_step_price

    total_price = int(base_price + devices_extra_price + traffic_extra_price)
    return total_price


async def start_tariff_config(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: Any,
    tariff_id: int,
):
    """Запускает конфигуратор тарифа по id."""
    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Указанный тариф не найден.",
        )
        await safe_answer_callback(callback_query)
        logger.warning(f"[TARIFF_CFG] start_tariff_config tariff_not_found: tariff_id={tariff_id}")
        return

    if not tariff.get("configurable"):
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Этот тариф нельзя настроить.",
        )
        await safe_answer_callback(callback_query)
        logger.info(f"[TARIFF_CFG] start_tariff_config not_configurable: tariff_id={tariff_id}")
        return

    await start_user_tariff_configurator(
        callback_query=callback_query,
        session=session,
        state=state,
        tariff=tariff,
    )


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
    from ...keys.key_create import create_key, moscow_tz

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


async def render_user_config_screen(
    callback_query: CallbackQuery,
    state: FSMContext,
    session: Any,
):
    """Рендерит экран конфигурации тарифа для пользователя."""
    data = await state.get_data()
    tariff_id = data.get("config_tariff_id")
    cfg = data.get("tariff_config") or {}

    if tariff_id is None and callback_query.data:
        parts = callback_query.data.split("|")
        if len(parts) >= 2 and (
            callback_query.data.startswith("cfg_user_devices|") or callback_query.data.startswith("cfg_user_traffic|")
        ):
            try:
                tariff_id = int(parts[1])
                await state.update_data(config_tariff_id=tariff_id)
            except (ValueError, IndexError):
                pass

    if tariff_id is None:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="Подождите, создаём подписку…",
            reply_markup=None,
        )
        await safe_answer_callback(callback_query)
        await state.clear()
        logger.warning("[TARIFF_CFG] render_user_config_screen: config_tariff_id missing in state")
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Тариф не найден.",
            reply_markup=None,
        )
        await safe_answer_callback(callback_query)
        await state.clear()
        logger.warning(f"[TARIFF_CFG] render_user_config_screen tariff_not_found: tariff_id={tariff_id}")
        return

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

    has_device_option = bool(device_int_options)
    has_traffic_option = bool(traffic_int_options)
    has_device_choice = len(device_int_options) > 1
    has_traffic_choice = len(traffic_int_options) > 1

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

    if has_device_option:
        if selected_devices is None:
            if base_device is not None and base_device in device_int_options:
                selected_devices = base_device
            elif device_int_options:
                selected_devices = device_int_options[0]
    else:
        selected_devices = None

    if has_traffic_option:
        if selected_traffic_gb is None:
            if base_traffic_gb is not None and base_traffic_gb in traffic_int_options:
                selected_traffic_gb = base_traffic_gb
            elif traffic_int_options:
                selected_traffic_gb = traffic_int_options[0]
    else:
        selected_traffic_gb = None

    final_price = calculate_config_price(
        tariff=tariff,
        selected_device_limit=int(selected_devices) if selected_devices is not None and has_device_option else None,
        selected_traffic_gb=int(selected_traffic_gb)
        if selected_traffic_gb is not None and has_traffic_option
        else None,
    )

    tg_id = callback_query.from_user.id
    language_code = getattr(callback_query.from_user, "language_code", None)
    price_text = await format_for_user(session, tg_id, float(final_price), language_code)

    base_parts = []
    if base_device is not None:
        if int(base_device) <= 0:
            base_devices_label = UNLIMITED_DEVICES_LABEL
        else:
            _bd = int(base_device)
            base_devices_label = f"{_bd} {get_plural_form(_bd, 'устройство', 'устройства', 'устройств')}"
        base_parts.append(base_devices_label)

    if base_traffic_gb is not None:
        if int(base_traffic_gb) <= 0:
            base_traffic_label = UNLIMITED_TRAFFIC_LABEL
        else:
            base_traffic_label = f"{int(base_traffic_gb)} ГБ"
        base_parts.append(base_traffic_label)

    if not base_parts:
        base_text = DEFAULT_LIMIT_LABEL
    else:
        base_text = ", ".join(base_parts)

    choice_parts = []

    if has_device_choice:
        if selected_devices is None:
            devices_label = DEFAULT_LIMIT_LABEL
        else:
            if int(selected_devices) <= 0:
                devices_label = UNLIMITED_DEVICES_LABEL
            else:
                _sd = int(selected_devices)
                devices_label = f"{_sd} {get_plural_form(_sd, 'устройство', 'устройства', 'устройств')}"
        choice_parts.append(devices_label)

    if has_traffic_choice:
        if selected_traffic_gb is None:
            traffic_label = DEFAULT_LIMIT_LABEL
        else:
            if selected_traffic_gb <= 0:
                traffic_label = UNLIMITED_TRAFFIC_LABEL
            else:
                traffic_label = f"{int(selected_traffic_gb)} ГБ"
        choice_parts.append(traffic_label)

    if not choice_parts:
        choice_text = DEFAULT_LIMIT_LABEL
    else:
        choice_text = ", ".join(choice_parts)

    addon_warning_text = ""
    if data.get("renew_mode") == "renew":
        _rc_dev = data.get("renew_current_device_limit")
        _rs_dev = data.get("renew_selected_device_limit")
        _rc_trf = data.get("renew_current_traffic_limit")
        _rs_trf = data.get("renew_selected_traffic_limit")
        _addon_parts: list[str] = []
        if _rc_dev is not None and _rs_dev is not None and int(_rc_dev) > int(_rs_dev):
            _diff_dev = int(_rc_dev) - int(_rs_dev)
            _addon_parts.append(f"+{_diff_dev} {get_plural_form(_diff_dev, 'устройство', 'устройства', 'устройств')}")
        if _rc_trf is not None and _rs_trf is not None and int(_rc_trf) > int(_rs_trf):
            _addon_parts.append(f"+{int(_rc_trf) - int(_rs_trf)} ГБ")
        if _addon_parts:
            addon_warning_text = ADDON_RESET_CONFIG_WARNING.format(addons=", ".join(_addon_parts))

    text = CONFIG_SCREEN_TEMPLATE.format(
        base=base_text,
        choice=choice_text,
        price=price_text,
        addon_warning=addon_warning_text,
    )

    builder = InlineKeyboardBuilder()

    def _dev_label(v: int) -> str:
        if v == 0:
            return UNLIMITED_DEVICES_LABEL.capitalize()
        return f"{v} устр."

    def _traf_label(v: int) -> str:
        if v == 0:
            return UNLIMITED_TRAFFIC_LABEL.capitalize()
        return f"{v} ГБ"

    def _stepper_row(options: list[int], selected, prefix: str, label_fn) -> list[InlineKeyboardButton]:
        cur = int(selected) if selected is not None else (options[0] if options else 0)
        try:
            idx = options.index(cur)
        except ValueError:
            idx = 0
        prev_val = options[idx - 1] if idx > 0 else options[idx]
        next_val = options[idx + 1] if idx < len(options) - 1 else options[idx]
        left = "◀️" if idx > 0 else "▫️"
        right = "▶️" if idx < len(options) - 1 else "▫️"
        return [
            InlineKeyboardButton(text=left, callback_data=f"{prefix}|{tariff_id}|{prev_val}"),
            InlineKeyboardButton(text=label_fn(options[idx]), callback_data=f"{prefix}|{tariff_id}|{options[idx]}"),
            InlineKeyboardButton(text=right, callback_data=f"{prefix}|{tariff_id}|{next_val}"),
        ]

    def _option_buttons(options: list[int], selected, prefix: str, label_fn) -> list[InlineKeyboardButton]:
        sel = int(selected) if selected is not None else None
        return [
            InlineKeyboardButton(
                text=label_fn(v) + (" ✅" if sel is not None and v == sel else ""),
                callback_data=f"{prefix}|{tariff_id}|{v}",
            )
            for v in options
        ]

    use_pagination = bool((MODES_CONFIG or {}).get("TARIFF_OPTIONS_PAGINATION", True))
    if use_pagination:
        if has_device_choice:
            builder.row(*_stepper_row(device_int_options, selected_devices, "cfg_user_devices", _dev_label))
        if has_traffic_choice:
            builder.row(*_stepper_row(traffic_int_options, selected_traffic_gb, "cfg_user_traffic", _traf_label))
    else:
        device_buttons = (
            _option_buttons(device_int_options, selected_devices, "cfg_user_devices", _dev_label)
            if has_device_choice
            else []
        )
        traffic_buttons = (
            _option_buttons(traffic_int_options, selected_traffic_gb, "cfg_user_traffic", _traf_label)
            if has_traffic_choice
            else []
        )
        if device_buttons and traffic_buttons:
            for i in range(max(len(device_buttons), len(traffic_buttons))):
                row = []
                if i < len(device_buttons):
                    row.append(device_buttons[i])
                if i < len(traffic_buttons):
                    row.append(traffic_buttons[i])
                builder.row(*row)
        elif device_buttons:
            for i in range(0, len(device_buttons), 2):
                builder.row(*device_buttons[i : i + 2])
        elif traffic_buttons:
            for i in range(0, len(traffic_buttons), 2):
                builder.row(*traffic_buttons[i : i + 2])

    is_renew_mode = data.get("renew_mode") == "renew"
    confirm_prefix = "cfg_renew_confirm" if is_renew_mode else "cfg_user_confirm"

    back_callback = "back_to_subgroup_tariffs" if data.get("tariff_subgroup_hash") else "back_to_tariff_group_list"
    builder.row(
        InlineKeyboardButton(
            text=CONFIG_PAY_BUTTON_TEXT.format(amount=price_text),
            callback_data=f"{confirm_prefix}|{tariff_id}",
        )
    )
    builder.row(InlineKeyboardButton(text=BACK, callback_data=back_callback))
    builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))

    await state.update_data(
        config_selected_device_limit=selected_devices,
        config_selected_traffic_gb=selected_traffic_gb,
    )

    await edit_or_send_message(
        target_message=callback_query.message,
        text=text,
        reply_markup=builder.as_markup(),
    )
    await safe_answer_callback(callback_query)


async def start_user_tariff_configurator(
    callback_query: CallbackQuery,
    session: Any,
    state: FSMContext,
    tariff: dict,
):
    """Запускает конфигуратор тарифа для пользователя."""
    cfg = normalize_tariff_config(tariff)

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

    logger.info(
        "[TARIFF_CFG] start_user_tariff_configurator: "
        f"tg_id={callback_query.from_user.id} tariff_id={tariff.get('id')} "
        f"device_options={device_options} traffic_options={traffic_options}"
    )

    if not device_options and not traffic_options:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Конфигуратор для этого тарифа не настроен. Попробуйте выбрать другой тариф.",
            reply_markup=None,
        )
        await safe_answer_callback(callback_query)
        logger.warning(f"[TARIFF_CFG] configurator_not_configured: tariff_id={tariff.get('id')}")
        return

    cfg_for_state = dict(cfg)
    cfg_for_state["device_options"] = device_options
    cfg_for_state["traffic_options_gb"] = traffic_options

    data = await state.get_data()
    renew_mode = data.get("renew_mode")

    update_payload: dict[str, Any] = {
        "config_tariff_id": tariff["id"],
        "tariff_config": cfg_for_state,
    }
    if renew_mode == "renew":
        update_payload["renew_tariff_id"] = tariff["id"]
    else:
        update_payload["config_selected_device_limit"] = None
        update_payload["config_selected_traffic_gb"] = None

    await state.update_data(**update_payload)
    await state.set_state(TariffUserConfigState.configuring)

    await render_user_config_screen(callback_query, state, session)


async def show_price_and_confirm(callback_query: CallbackQuery, state: FSMContext, session: Any):
    """Обновляет экран конфигурации и показывает актуальную цену."""
    await render_user_config_screen(callback_query, state, session)


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


@router.callback_query(F.data.startswith("select_tariff_plan|"))
async def select_tariff_plan(callback_query: CallbackQuery, session: Any, state: FSMContext):
    """Обрабатывает выбор тарифа пользователем."""
    tg_id = callback_query.from_user.id
    tariff_id = int(callback_query.data.split("|")[1])

    logger.info("[TARIFF_CFG] select_tariff_plan: tg_id={} tariff_id={}", tg_id, tariff_id)

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await edit_or_send_message(
            target_message=callback_query.message,
            text="❌ Указанный тариф не найден.",
        )
        await safe_answer_callback(callback_query)
        logger.warning(f"[TARIFF_CFG] select_tariff_plan tariff_not_found: tariff_id={tariff_id}")
        return

    cooldown_left = await get_tariff_cooldown_remaining(
        session, tg_id, tariff.get("id"), tariff.get("cooldown_days", 0)
    )
    if cooldown_left > 0:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=TARIFF_COOLDOWN_MESSAGE.format(
                days=int(tariff.get("cooldown_days") or 0),
                left=format_cooldown_left(cooldown_left),
            ),
            reply_markup=builder.as_markup(),
        )
        await safe_answer_callback(callback_query)
        logger.info(
            f"[TARIFF_CFG] select_tariff_plan cooldown_block: tg_id={tg_id} tariff_id={tariff_id} left={cooldown_left}s"
        )
        return

    discount_info = await check_hot_lead_discount(session, tg_id)
    if tariff.get("group_code") in ["discounts", "discounts_max"]:
        if not discount_info.get("available") or datetime.now(timezone.utc) >= discount_info["expires_at"]:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text="❌ Скидка недоступна или истекла. Пожалуйста, выберите тариф заново.",
                reply_markup=builder.as_markup(),
            )
            await safe_answer_callback(callback_query)
            logger.info(
                "[TARIFF_CFG] select_tariff_plan discount_invalid: "
                f"tg_id={tg_id} tariff_id={tariff_id} info={discount_info}"
            )
            return
    elif tariff.get("group_code") in ["cold_discounts", "cold_discounts_max"]:
        cold_discount_info = await check_cold_lead_discount(session, tg_id)
        if not cold_discount_info.get("available") or datetime.now(timezone.utc) >= cold_discount_info["expires_at"]:
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
            await edit_or_send_message(
                target_message=callback_query.message,
                text="❌ Скидка недоступна или истекла. Пожалуйста, выберите тариф заново.",
                reply_markup=builder.as_markup(),
            )
            await safe_answer_callback(callback_query)
            logger.info(
                "[TARIFF_CFG] select_tariff_plan cold_discount_invalid: "
                f"tg_id={tg_id} tariff_id={tariff_id} info={cold_discount_info}"
            )
            return

    validity_result = await process_check_discount_validity(
        chat_id=tg_id,
        admin=False,
        session=session,
        tariff_group=tariff.get("group_code"),
    )
    if validity_result:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=MAIN_MENU, callback_data="profile"))
        await edit_or_send_message(
            target_message=callback_query.message,
            text=validity_result["message"],
            reply_markup=builder.as_markup(),
        )
        await safe_answer_callback(callback_query)
        logger.info(
            "[TARIFF_CFG] select_tariff_plan discount_validity_failed: "
            f"tg_id={tg_id} tariff_id={tariff_id} result={validity_result}"
        )
        return

    data = await state.get_data()
    if data.get("renew_mode") != "renew":
        await state.update_data(renew_mode=None)

    if tariff.get("configurable"):
        logger.info(f"[TARIFF_CFG] select_tariff_plan configurable: tg_id={tg_id} tariff_id={tariff_id}")
        try:
            await start_user_tariff_configurator(callback_query, session=session, state=state, tariff=tariff)
        except Exception as error:
            logger.error(f"[TARIFF_CFG] error_in_configurator: tariff_id={tariff_id} error={error}")
            await edit_or_send_message(
                target_message=callback_query.message,
                text="❌ Ошибка конфигурации тарифа. Попробуйте позже.",
                reply_markup=None,
            )
        return

    duration_days = int(tariff.get("duration_days") or 30)
    price_rub = int(tariff.get("price_rub") or 0)
    selected_device_limit = tariff.get("device_limit")

    raw_traffic_limit = tariff.get("traffic_limit")
    selected_traffic_gb = None
    if raw_traffic_limit:
        raw_traffic_limit = int(raw_traffic_limit)
        if raw_traffic_limit >= GB:
            selected_traffic_gb = int(raw_traffic_limit / GB)
        else:
            selected_traffic_gb = raw_traffic_limit

    logger.info(
        "[TARIFF_CFG] select_tariff_plan fixed_tariff: "
        f"tg_id={tg_id} tariff_id={tariff_id} duration_days={duration_days} "
        f"price_rub={price_rub} device_limit={selected_device_limit} "
        f"selected_traffic_gb={selected_traffic_gb}"
    )

    await proceed_purchase_with_values(
        callback_query=callback_query,
        session=session,
        state=state,
        tariff=tariff,
        duration_days=duration_days,
        price_rub=price_rub,
        selected_device_limit=selected_device_limit,
        selected_traffic_gb=selected_traffic_gb,
    )


@router.callback_query(
    F.data.startswith("cfg_user_devices|"),
    TariffUserConfigState.configuring,
)
async def handle_user_devices_choice(callback: CallbackQuery, state: FSMContext, session: Any):
    """Обрабатывает выбор лимита устройств в конфигураторе."""
    await safe_answer_callback(callback)
    _, _tariff_id_str, devices_str = callback.data.split("|", 2)
    devices = int(devices_str)

    data = await state.get_data()
    if data.get("config_tariff_id") is None:
        await state.update_data(config_tariff_id=int(_tariff_id_str))
    current = data.get("config_selected_device_limit")
    if current is not None and int(current) == devices:
        return

    await state.update_data(config_selected_device_limit=devices)
    await render_user_config_screen(callback, state, session)


@router.callback_query(
    F.data.startswith("cfg_user_traffic|"),
    TariffUserConfigState.configuring,
)
async def handle_user_traffic_choice(callback: CallbackQuery, state: FSMContext, session: Any):
    """Обрабатывает выбор лимита трафика в конфигураторе."""
    await safe_answer_callback(callback)
    _, _tariff_id_str, traffic_str = callback.data.split("|", 2)
    traffic = int(traffic_str)

    data = await state.get_data()
    if data.get("config_tariff_id") is None:
        await state.update_data(config_tariff_id=int(_tariff_id_str))
    current = data.get("config_selected_traffic_gb")
    if current is not None and int(current) == traffic:
        return

    await state.update_data(config_selected_traffic_gb=traffic)
    await render_user_config_screen(callback, state, session=session)


@router.callback_query(F.data.startswith("cfg_user_confirm|"), TariffUserConfigState.configuring)
async def handle_user_config_confirm(callback: CallbackQuery, state: FSMContext, session: Any):
    """Подтверждает выбор параметров тарифа и запускает покупку."""
    logger.info(f"[TARIFF_CFG] handle_user_config_confirm: tg_id={callback.from_user.id}")
    await finalize_config_and_purchase(callback, state, session=session)
