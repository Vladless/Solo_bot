
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from core.settings.tariffs_config import TARIFFS_CONFIG
from database import get_tariff_by_id
from handlers.utils import edit_or_send_message, get_plural_form
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import process_addons_menu
from logger import logger
from services.payments.currency_rates import format_for_user
from services.tariffs.pricing import calculate_config_price
from settings.buttons import (
    BACK,
    CONFIRM_ADDON_BUTTON_TEXT,
    DOWNGRADE_ADDON_BUTTON_TEXT,
)
from settings.texts import (
    DOWNGRADE_INLINE_WARNING_TEXT,
    UNLIMITED_DEVICES_LABEL,
    UNLIMITED_TRAFFIC_LABEL,
)

from ....keys.utils import build_key_callback
from ..utils import (
    build_addons_screen_text,
    format_devices_label,
    format_traffic_label,
    is_not_downgrade,
)


router = Router()


async def render_addons_screen(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    email = data.get("addon_key_email")
    tariff_id = data.get("addon_tariff_id")
    cfg = data.get("addon_tariff_config") or {}

    logger.debug(
        f"[ADDONS] render_addons_screen start: tg_id={callback.from_user.id} "
        f"email={email} tariff_id={tariff_id} data={data}"
    )

    current_devices = data.get("addon_current_device_limit")
    current_traffic_gb = data.get("addon_current_traffic_gb")
    original_price = int(data.get("addon_original_price") or 0)

    selected_devices = data.get("addon_selected_device_limit")
    selected_traffic_gb = data.get("addon_selected_traffic_gb")

    if not email or not tariff_id:
        logger.warning(f"[ADDONS] Нет email или tariff_id в состоянии: {data}")
        await callback.message.answer("❌ Данные для изменения подписки не найдены.")
        await state.clear()
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(f"[ADDONS] Тариф {tariff_id} не найден в render_addons_screen")
        await callback.message.answer("❌ Тариф не найден.")
        await state.clear()
        return

    tariff_name = tariff.get("name") or "подписка"

    raw_device_options = cfg.get("device_options") or []
    raw_traffic_options = cfg.get("traffic_options_gb") or []

    try:
        device_options = sorted(
            raw_device_options,
            key=lambda v: (int(v) == 0, int(v)),
        )
    except (TypeError, ValueError):
        device_options = raw_device_options

    try:
        traffic_options = sorted(
            raw_traffic_options,
            key=lambda v: (int(v) == 0, int(v)),
        )
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

    if selected_devices is None and has_device_option:
        if current_devices is not None and int(current_devices) in device_int_options:
            selected_devices = int(current_devices)
        elif device_int_options:
            selected_devices = device_int_options[0]

    if selected_traffic_gb is None and has_traffic_option:
        if current_traffic_gb is not None and int(current_traffic_gb) in traffic_int_options:
            selected_traffic_gb = int(current_traffic_gb)
        elif traffic_int_options:
            selected_traffic_gb = traffic_int_options[0]

    logger.debug(
        "[ADDONS] Limits before price: "
        f"current_devices={current_devices} current_traffic_gb={current_traffic_gb} "
        f"selected_devices={selected_devices} selected_traffic_gb={selected_traffic_gb} "
        f"original_price={original_price}"
    )

    await state.update_data(
        addon_selected_device_limit=selected_devices,
        addon_selected_traffic_gb=selected_traffic_gb,
    )

    current_devices_for_price = int(current_devices) if current_devices is not None and has_device_option else None
    current_traffic_for_price = (
        int(current_traffic_gb) if current_traffic_gb is not None and has_traffic_option else None
    )
    base_price_for_current = calculate_config_price(
        tariff=tariff,
        selected_device_limit=current_devices_for_price,
        selected_traffic_gb=current_traffic_for_price,
    )

    total_price = calculate_config_price(
        tariff=tariff,
        selected_device_limit=int(selected_devices) if selected_devices is not None and has_device_option else None,
        selected_traffic_gb=int(selected_traffic_gb)
        if selected_traffic_gb is not None and has_traffic_option
        else None,
    )
    extra_price = max(0, total_price - base_price_for_current)

    logger.debug(
        "[ADDONS] Calculated prices: "
        f"base_price_for_current={base_price_for_current} total_price={total_price} extra_price={extra_price} "
        f"has_device_option={has_device_option} has_traffic_option={has_traffic_option}"
    )

    tg_id = callback.from_user.id
    language_code = getattr(callback.from_user, "language_code", None)

    total_price_text = await format_for_user(session, tg_id, float(total_price), language_code)
    extra_price_text = await format_for_user(session, tg_id, float(extra_price), language_code)

    current_devices_label = format_devices_label(current_devices)
    current_traffic_label = format_traffic_label(current_traffic_gb)
    new_devices_label = format_devices_label(selected_devices)
    new_traffic_label = format_traffic_label(selected_traffic_gb)

    downgrade_warning = None
    devices_downgrade = False
    traffic_downgrade = False
    allow_downgrade = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))

    if allow_downgrade:
        if has_device_choice and current_devices is not None and selected_devices is not None:
            devices_downgrade = not is_not_downgrade(current_devices, selected_devices)
        if has_traffic_choice and current_traffic_gb is not None and selected_traffic_gb is not None:
            traffic_downgrade = not is_not_downgrade(current_traffic_gb, selected_traffic_gb)
        if devices_downgrade or traffic_downgrade:
            new_limits_parts = []
            if has_device_choice:
                new_limits_parts.append(new_devices_label)
            if has_traffic_choice:
                new_limits_parts.append(new_traffic_label)
            new_limits_desc = ", ".join(new_limits_parts) if new_limits_parts else "выбранные параметры"
            downgrade_warning = DOWNGRADE_INLINE_WARNING_TEXT.format(
                total_price_text=total_price_text,
                new_limits_desc=new_limits_desc,
            )

    logger.debug(
        "[ADDONS] Downgrade flags: "
        f"devices_downgrade={devices_downgrade} traffic_downgrade={traffic_downgrade} "
        f"ALLOW_DOWNGRADE={allow_downgrade}"
    )

    text = build_addons_screen_text(
        tariff_name=tariff_name,
        current_devices_label=current_devices_label,
        current_traffic_label=current_traffic_label,
        new_devices_label=new_devices_label,
        new_traffic_label=new_traffic_label,
        has_device_choice=has_device_choice,
        has_traffic_choice=has_traffic_choice,
        total_price_text=total_price_text,
        extra_price_text=extra_price_text,
        downgrade_warning=downgrade_warning,
    )

    builder = InlineKeyboardBuilder()

    def _dev_label(v: int) -> str:
        if int(v) == 0:
            return UNLIMITED_DEVICES_LABEL.capitalize()
        return f"{v} {get_plural_form(v, 'устройство', 'устройства', 'устройств')}"

    def _traf_label(v: int) -> str:
        if int(v) == 0:
            return UNLIMITED_TRAFFIC_LABEL.capitalize()
        return f"{v} ГБ"

    allowed_devices = (
        [v for v in device_int_options if allow_downgrade or is_not_downgrade(current_devices, v)]
        if has_device_choice
        else []
    )
    allowed_traffic = (
        [v for v in traffic_int_options if allow_downgrade or is_not_downgrade(current_traffic_gb, v)]
        if has_traffic_choice
        else []
    )

    def _addon_stepper_row(options, selected, cb_prefix, label_fn):
        try:
            options = sorted(options, key=lambda v: (int(v) == 0, int(v)))
        except (TypeError, ValueError):
            pass
        if not options:
            return []
        cur = int(selected) if selected is not None else options[0]
        try:
            idx = options.index(cur)
        except ValueError:
            idx = 0
        prev_val = options[idx - 1] if idx > 0 else options[idx]
        next_val = options[idx + 1] if idx < len(options) - 1 else options[idx]
        left = "◀️" if idx > 0 else "▫️"
        right = "▶️" if idx < len(options) - 1 else "▫️"
        return [
            InlineKeyboardButton(text=left, callback_data=f"{cb_prefix}|{email}|{prev_val}"),
            InlineKeyboardButton(text=label_fn(options[idx]), callback_data=f"{cb_prefix}|{email}|{options[idx]}"),
            InlineKeyboardButton(text=right, callback_data=f"{cb_prefix}|{email}|{next_val}"),
        ]

    use_pagination = bool((MODES_CONFIG or {}).get("TARIFF_OPTIONS_PAGINATION", True))
    if use_pagination:
        if allowed_devices:
            builder.row(*_addon_stepper_row(allowed_devices, selected_devices, "key_addons_devices", _dev_label))
        if allowed_traffic:
            builder.row(*_addon_stepper_row(allowed_traffic, selected_traffic_gb, "key_addons_traffic", _traf_label))
    else:
        device_buttons = [
            InlineKeyboardButton(
                text=_dev_label(v) + (" ✅" if selected_devices is not None and int(v) == int(selected_devices) else ""),
                callback_data=f"key_addons_devices|{email}|{v}",
            )
            for v in allowed_devices
        ]
        traffic_buttons = [
            InlineKeyboardButton(
                text=_traf_label(v)
                + (" ✅" if selected_traffic_gb is not None and int(v) == int(selected_traffic_gb) else ""),
                callback_data=f"key_addons_traffic|{email}|{v}",
            )
            for v in allowed_traffic
        ]
        if device_buttons and traffic_buttons:
            max_len = max(len(device_buttons), len(traffic_buttons))
            for i in range(max_len):
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

    if allow_downgrade and (devices_downgrade or traffic_downgrade):
        builder.row(
            InlineKeyboardButton(
                text=DOWNGRADE_ADDON_BUTTON_TEXT,
                callback_data="key_addons_downgrade",
            )
        )
    else:
        builder.row(
            InlineKeyboardButton(
                text=CONFIRM_ADDON_BUTTON_TEXT.format(amount=extra_price_text),
                callback_data="key_addons_confirm",
            )
        )

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=build_key_callback("view_key", data.get("addon_key_client_id"), email),
        )
    )

    module_buttons = await process_addons_menu(email=email, session=session)
    builder = insert_hook_buttons(builder, module_buttons)

    await edit_or_send_message(
        target_message=callback.message,
        text=text,
        reply_markup=builder.as_markup(),
    )
    await callback.answer()
