from typing import Any

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.bootstrap import MODES_CONFIG
from database import get_tariff_by_id
from handlers.utils import edit_or_send_message, get_plural_form, safe_answer_callback
from logger import logger
from services.payments.currency_rates import format_for_user
from services.addons import calc_carried_addons_price_rub, is_addons_carry_enabled
from services.tariffs.pricing import calculate_config_price
from services.tariffs.tariff_display import GB
from settings.buttons import BACK, CONFIG_PAY_BUTTON_TEXT, MAIN_MENU
from settings.texts import (
    ADDON_CARRY_CONFIG_NOTICE,
    ADDON_RESET_CONFIG_WARNING,
    CONFIG_SCREEN_TEMPLATE,
    DEFAULT_LIMIT_LABEL,
    UNLIMITED_DEVICES_LABEL,
    UNLIMITED_TRAFFIC_LABEL,
)


CREATING_KEY_BUTTON_TEXT = "⏳ Подождите..."


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

    carried_price = 0
    if data.get("renew_mode") == "renew" and is_addons_carry_enabled():
        _base_dev = data.get("renew_selected_device_limit")
        _base_trf = data.get("renew_selected_traffic_limit")
        if selected_devices == _base_dev and selected_traffic_gb == _base_trf:
            carried_price = calc_carried_addons_price_rub(
                tariff,
                _base_dev,
                _base_trf,
                data.get("renew_current_device_limit"),
                data.get("renew_current_traffic_limit"),
            )
            final_price += carried_price

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
        if _addon_parts and carried_price:
            addon_warning_text = ADDON_CARRY_CONFIG_NOTICE.format(
                addons=", ".join(_addon_parts),
                price=await format_for_user(session, tg_id, float(carried_price), language_code),
            )
        elif _addon_parts:
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


async def show_price_and_confirm(callback_query: CallbackQuery, state: FSMContext, session: Any):
    """Обновляет экран конфигурации и показывает актуальную цену."""
    await render_user_config_screen(callback_query, state, session)
