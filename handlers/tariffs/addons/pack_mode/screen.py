

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from core.settings.tariffs_config import TARIFFS_CONFIG
from database import (
    get_tariff_by_id,
)
from handlers.utils import edit_or_send_message, get_plural_form
from hooks.hook_buttons import insert_hook_buttons
from hooks.processors import process_addons_menu
from logger import logger
from services.addons import (
    calc_pack_full_price_rub,
    get_pack_flags,
)
from services.payments.currency_rates import format_for_user
from services.tariffs.pricing import calculate_config_price
from settings.buttons import BACK, CONFIRM_ADDON_BUTTON_TEXT
from settings.texts import (
    UNLIMITED_DEVICES_LABEL,
    UNLIMITED_TRAFFIC_LABEL,
)

from ....keys.utils import build_key_callback
from ..utils import (
    build_addons_pack_screen_text,
    calc_remaining_ratio_seconds,
    format_devices_label,
    format_traffic_label,
)


router = Router()


async def render_addons_screen(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    email = data.get("addon_key_email")
    tariff_id = data.get("addon_tariff_id")
    cfg = data.get("addon_tariff_config") or {}

    logger.debug(
        f"[ADDONS] render_addons_screen PACK_MODE start: tg_id={callback.from_user.id} "
        f"email={email} tariff_id={tariff_id} data={data}"
    )

    current_devices = data.get("addon_current_device_limit")
    current_traffic_gb = data.get("addon_current_traffic_gb")
    selected_devices = data.get("addon_selected_device_limit")
    selected_traffic_gb = data.get("addon_selected_traffic_gb")
    expiry_time = data.get("addon_expiry_time")

    if not email or not tariff_id:
        logger.warning(f"[ADDONS] PACK_MODE: нет email или tariff_id в состоянии: {data}")
        await callback.message.answer("❌ Данные для изменения подписки не найдены.")
        await state.clear()
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(f"[ADDONS] PACK_MODE: тариф {tariff_id} не найден в render_addons_screen")
        await callback.message.answer("❌ Тариф не найден.")
        await state.clear()
        return

    tariff_name = tariff.get("name") or "подписка"

    raw_device_options = cfg.get("device_options") or tariff.get("device_options") or []
    raw_traffic_options = cfg.get("traffic_options_gb") or tariff.get("traffic_options_gb") or []

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

    pack_devices, pack_traffic, pack_mode = get_pack_flags()

    has_device_option = pack_devices and bool(device_int_options)
    has_traffic_option = pack_traffic and bool(traffic_int_options)

    if has_device_option and current_devices is not None and int(current_devices) == 0:
        has_device_option = False
        selected_devices = None

    if has_traffic_option and current_traffic_gb is not None and int(current_traffic_gb) == 0:
        has_traffic_option = False
        selected_traffic_gb = None

    if not has_device_option:
        selected_devices = None
    if not has_traffic_option:
        selected_traffic_gb = None

    if selected_devices is None and has_device_option and device_int_options:
        selected_devices = device_int_options[0]
    if selected_traffic_gb is None and has_traffic_option and traffic_int_options:
        selected_traffic_gb = traffic_int_options[0]

    await state.update_data(
        addon_selected_device_limit=selected_devices,
        addon_selected_traffic_gb=selected_traffic_gb,
    )

    current_devices_for_price = int(current_devices) if current_devices is not None else None
    current_traffic_for_price = int(current_traffic_gb) if current_traffic_gb is not None else None

    base_price_for_current = calculate_config_price(
        tariff=tariff,
        selected_device_limit=current_devices_for_price,
        selected_traffic_gb=current_traffic_for_price,
    )
    try:
        base_price_for_current_int = int(base_price_for_current) if base_price_for_current is not None else 0
    except (TypeError, ValueError):
        base_price_for_current_int = 0

    recalc_enabled = bool(
        MODES_CONFIG.get(
            "KEY_ADDONS_RECALC_PRICE",
            TARIFFS_CONFIG.get("KEY_ADDONS_RECALC_PRICE", False),
        )
    )

    diff_full = calc_pack_full_price_rub(
        tariff=tariff,
        has_device_option=has_device_option,
        has_traffic_option=has_traffic_option,
        selected_devices=int(selected_devices) if selected_devices is not None else None,
        selected_traffic_gb=int(selected_traffic_gb) if selected_traffic_gb is not None else None,
    )

    if recalc_enabled:
        remaining_seconds, total_seconds = calc_remaining_ratio_seconds(expiry_time, tariff)
        extra_price = int((diff_full * remaining_seconds + total_seconds - 1) // total_seconds)
    else:
        extra_price = int(diff_full)

    logger.debug(
        "[ADDONS] PACK_MODE calculated prices: "
        f"base_price_for_current={base_price_for_current_int} diff_full={diff_full} "
        f"extra_price={extra_price} recalc_enabled={recalc_enabled} "
        f"has_device_option={has_device_option} has_traffic_option={has_traffic_option} pack_mode={pack_mode!r}"
    )

    tg_id = callback.from_user.id
    language_code = getattr(callback.from_user, "language_code", None)

    extra_price_text = await format_for_user(session, tg_id, float(extra_price), language_code)

    current_devices_label = format_devices_label(current_devices)
    current_traffic_label = format_traffic_label(current_traffic_gb)

    has_device_pack_selected = has_device_option and selected_devices is not None
    has_traffic_pack_selected = has_traffic_option and selected_traffic_gb is not None

    selected_devices_label = format_devices_label(selected_devices) if has_device_pack_selected else None
    selected_traffic_label = format_traffic_label(selected_traffic_gb) if has_traffic_pack_selected else None

    if has_device_pack_selected:
        current_devices_value = int(current_devices) if current_devices else 0
        selected_devices_value = int(selected_devices)
        total_devices_value = (
            0
            if current_devices_value <= 0 or selected_devices_value <= 0
            else current_devices_value + selected_devices_value
        )
        total_devices_label = format_devices_label(total_devices_value)
    else:
        total_devices_label = None

    if has_traffic_pack_selected:
        current_traffic_value = int(current_traffic_gb) if current_traffic_gb else 0
        selected_traffic_value = int(selected_traffic_gb)
        total_after_gb = (
            0
            if current_traffic_value <= 0 or selected_traffic_value <= 0
            else current_traffic_value + selected_traffic_value
        )
        total_traffic_label = format_traffic_label(total_after_gb)
    else:
        total_traffic_label = None

    text = build_addons_pack_screen_text(
        tariff_name=tariff_name,
        current_devices_label=current_devices_label,
        current_traffic_label=current_traffic_label if current_traffic_gb is not None else None,
        selected_devices_label=selected_devices_label,
        selected_traffic_label=selected_traffic_label,
        total_devices_label=total_devices_label,
        total_traffic_label=total_traffic_label,
        extra_price_text=extra_price_text,
        has_device_option=has_device_option,
        has_traffic_option=has_traffic_option,
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

    def _addon_stepper_row(options, selected, cb_prefix, label_fn):
        try:
            options = sorted(options, key=lambda v: (int(v) == 0, int(v)))
        except (TypeError, ValueError):
            pass
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
            InlineKeyboardButton(text=left, callback_data=f"{cb_prefix}|{email}|{prev_val}"),
            InlineKeyboardButton(text=label_fn(options[idx]), callback_data=f"{cb_prefix}|{email}|{options[idx]}"),
            InlineKeyboardButton(text=right, callback_data=f"{cb_prefix}|{email}|{next_val}"),
        ]

    use_pagination = bool((MODES_CONFIG or {}).get("TARIFF_OPTIONS_PAGINATION", True))
    if use_pagination:
        if has_device_option:
            builder.row(*_addon_stepper_row(device_int_options, selected_devices, "key_addons_devices", _dev_label))
        if has_traffic_option:
            builder.row(*_addon_stepper_row(traffic_int_options, selected_traffic_gb, "key_addons_traffic", _traf_label))
    else:
        device_buttons = []
        traffic_buttons = []
        if has_device_option:
            for value in device_int_options:
                mark = " ✅" if selected_devices is not None and int(value) == int(selected_devices) else ""
                device_buttons.append(
                    InlineKeyboardButton(
                        text=_dev_label(value) + mark,
                        callback_data=f"key_addons_devices|{email}|{value}",
                    )
                )
        if has_traffic_option:
            for value in traffic_int_options:
                mark = " ✅" if selected_traffic_gb is not None and int(selected_traffic_gb) == int(value) else ""
                traffic_buttons.append(
                    InlineKeyboardButton(
                        text=_traf_label(value) + mark,
                        callback_data=f"key_addons_traffic|{email}|{value}",
                    )
                )
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
