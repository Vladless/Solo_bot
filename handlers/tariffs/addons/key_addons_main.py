from math import ceil

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config import USE_NEW_PAYMENT_FLOW
from core.settings.tariffs_config import TARIFFS_CONFIG, normalize_tariff_config
from database import get_balance, get_key_details, get_tariff_by_id, save_key_config_with_mode, update_balance
from handlers.buttons import (
    BACK,
    CONFIRM_ADDON_BUTTON_TEXT,
    DOWNGRADE_ADDON_BUTTON_TEXT,
    DOWNGRADE_CONFIRM_BUTTON_TEXT,
    PAYMENT,
)
from handlers.keys.key_view import render_key_info
from handlers.payments.currency_rates import format_for_user
from handlers.payments.fast_payment_flow import try_fast_payment_flow
from handlers.tariffs.tariff_display import GB, get_effective_limits_for_key
from handlers.texts import (
    ADDONS_APPLIED_TEXT,
    DOWNGRADE_INLINE_WARNING_TEXT,
    DOWNGRADE_SAVED_TEXT,
    DOWNGRADE_WARNING_TEXT,
    INSUFFICIENT_FUNDS_RENEWAL_MSG,
    NO_EXTRA_PAYMENT_TEXT,
    UNLIMITED_DEVICES_LABEL,
    UNLIMITED_TRAFFIC_LABEL,
)
from handlers.utils import edit_or_send_message
from logger import logger

from ..buy.key_tariffs import calculate_config_price
from .utils import (
    KeyAddonConfigState,
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

    has_device_option = bool(device_int_options)
    has_device_choice = len(device_int_options) > 1

    has_traffic_option = bool(traffic_int_options)
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
    device_buttons = []
    traffic_buttons = []

    if has_device_choice:
        for value in device_int_options:
            if not allow_downgrade and not is_not_downgrade(current_devices, value):
                continue
            is_selected = selected_devices is not None and int(value) == int(selected_devices)
            mark = " ✅" if is_selected else ""
            if value == 0:
                caption = f"{UNLIMITED_DEVICES_LABEL.capitalize()}{mark}"
            else:
                caption = f"{value} устройств{mark}"
            device_buttons.append(
                InlineKeyboardButton(
                    text=caption,
                    callback_data=f"key_addons_devices|{email}|{value}",
                )
            )

    if has_traffic_choice:
        for value in traffic_int_options:
            if not allow_downgrade and not is_not_downgrade(current_traffic_gb, value):
                continue
            is_selected = selected_traffic_gb is not None and int(selected_traffic_gb) == int(value)
            mark = " ✅" if is_selected else ""
            if value == 0:
                caption = f"{UNLIMITED_TRAFFIC_LABEL.capitalize()}{mark}"
            else:
                caption = f"{value} ГБ{mark}"
            traffic_buttons.append(
                InlineKeyboardButton(
                    text=caption,
                    callback_data=f"key_addons_traffic|{email}|{value}",
                )
            )

    logger.debug(
        f"[ADDONS] Buttons: devices={[b.text for b in device_buttons]} traffic={[b.text for b in traffic_buttons]}"
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
        for button in device_buttons:
            builder.row(button)
    elif traffic_buttons:
        for button in traffic_buttons:
            builder.row(button)

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

    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"view_key|{email}"))

    await edit_or_send_message(
        target_message=callback.message,
        text=text,
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("key_addons|"))
async def start_key_addons(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    email = callback.data.split("|")[1]
    logger.debug(f"[ADDONS] start_key_addons: tg_id={callback.from_user.id} email={email}")

    record = await get_key_details(session, email)
    if not record:
        logger.warning(f"[ADDONS] Подписка {email} не найдена")
        await callback.message.answer("❌ Подписка не найдена.")
        return

    tariff_id = record.get("tariff_id")
    if not tariff_id:
        logger.warning(f"[ADDONS] Для подписки {email} не назначен тариф")
        await callback.message.answer("❌ Для этой подписки тариф не назначен, расширение недоступно.")
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(f"[ADDONS] Тариф {tariff_id} не найден для email={email}")
        await callback.message.answer("❌ Тариф не найден.")
        return

    if not tariff.get("configurable"):
        logger.info(f"[ADDONS] Тариф {tariff_id} не конфигурируемый, расширение недоступно")
        await callback.message.answer("❌ Для этого тарифа расширение через конфигуратор недоступно.")
        return

    cfg = normalize_tariff_config(tariff)

    raw_device_options = cfg.get("device_options") or tariff.get("device_options") or []
    raw_traffic_options = cfg.get("traffic_options_gb") or tariff.get("traffic_options_gb") or []

    device_int_options: list[int] = []
    for value in raw_device_options:
        try:
            device_int_options.append(int(value))
        except (TypeError, ValueError):
            continue

    traffic_int_options: list[int] = []
    for value in raw_traffic_options:
        try:
            traffic_int_options.append(int(value))
        except (TypeError, ValueError):
            continue

    base_device_limit = cfg.get("base_device_limit")
    if base_device_limit is None:
        base_device_limit = tariff.get("device_limit")
    if base_device_limit is not None:
        try:
            base_device_int = int(base_device_limit)
            if base_device_int not in device_int_options:
                raw_device_options.append(base_device_int)
                device_int_options.append(base_device_int)
        except (TypeError, ValueError):
            pass

    device_overrides_cfg = cfg.get("device_price_overrides") or tariff.get("device_overrides") or {}
    if "0" in device_overrides_cfg and 0 not in device_int_options:
        raw_device_options.append(0)
        device_int_options.append(0)

    base_traffic_gb = cfg.get("base_traffic_gb")
    if base_traffic_gb is None:
        raw_limit = tariff.get("traffic_limit")
        if raw_limit:
            raw_limit = int(raw_limit)
            if raw_limit >= GB:
                base_traffic_gb = int(raw_limit / GB)
            else:
                base_traffic_gb = raw_limit
    if base_traffic_gb is not None:
        try:
            base_traffic_int = int(base_traffic_gb)
            if base_traffic_int not in traffic_int_options:
                raw_traffic_options.append(base_traffic_int)
                traffic_int_options.append(base_traffic_int)
        except (TypeError, ValueError):
            pass

    traffic_overrides_cfg = cfg.get("traffic_price_overrides") or tariff.get("traffic_overrides") or {}
    if "0" in traffic_overrides_cfg and 0 not in traffic_int_options:
        raw_traffic_options.append(0)
        traffic_int_options.append(0)

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

    logger.info(
        "[ADDONS] start_key_addons options: "
        f"email={email} tariff_id={tariff_id} "
        f"device_options={device_options} traffic_options={traffic_options}"
    )

    if not device_options and not traffic_options:
        logger.warning(f"[ADDONS] Пустой конфигуратор для тарифа {tariff_id}")
        await callback.message.answer("❌ Конфигуратор для этого тарифа не настроен.")
        return

    selected_device_limit_db = record.get("selected_device_limit")
    selected_traffic_limit_db = record.get("selected_traffic_limit")
    original_price_db = record.get("selected_price_rub")
    current_device_limit_db = record.get("current_device_limit")
    current_traffic_limit_db = record.get("current_traffic_limit")

    base_devices = tariff.get("device_limit")
    base_devices = int(base_devices) if base_devices is not None else None

    base_traffic_bytes = tariff.get("traffic_limit")
    base_traffic_gb_from_tariff = int(base_traffic_bytes / GB) if base_traffic_bytes else None

    current_devices = (
        int(current_device_limit_db)
        if current_device_limit_db is not None
        else (int(selected_device_limit_db) if selected_device_limit_db is not None else base_devices)
    )
    current_traffic_gb = (
        int(current_traffic_limit_db)
        if current_traffic_limit_db is not None
        else (int(selected_traffic_limit_db) if selected_traffic_limit_db is not None else base_traffic_gb_from_tariff)
    )

    current_devices_for_price = int(current_devices) if current_devices is not None else None
    current_traffic_gb_for_price = int(current_traffic_gb) if current_traffic_gb is not None else None

    config_price_for_current = calculate_config_price(
        tariff=tariff,
        selected_device_limit=current_devices_for_price,
        selected_traffic_gb=current_traffic_gb_for_price,
    )

    try:
        original_price_from_db = int(original_price_db) if original_price_db is not None else 0
    except (TypeError, ValueError):
        original_price_from_db = 0

    original_price = original_price_from_db or int(config_price_for_current)

    logger.debug(
        "[ADDONS] start_key_addons state: "
        f"email={email} tariff_id={tariff_id} current_devices={current_devices} current_traffic_gb={current_traffic_gb} "
        f"config_price_for_current={config_price_for_current} original_price_db={original_price_db} "
        f"original_price={original_price}"
    )

    cfg_for_state = dict(cfg)
    if device_options:
        cfg_for_state["device_options"] = device_options
    if traffic_options:
        cfg_for_state["traffic_options_gb"] = traffic_options

    await state.update_data(
        addon_key_email=email,
        addon_tariff_id=int(tariff_id),
        addon_tariff_config=cfg_for_state,
        addon_current_device_limit=current_devices,
        addon_current_traffic_gb=current_traffic_gb,
        addon_original_price=original_price,
        addon_selected_device_limit=current_devices,
        addon_selected_traffic_gb=current_traffic_gb,
    )
    await state.set_state(KeyAddonConfigState.configuring)

    await render_addons_screen(callback, state, session)


@router.callback_query(F.data.startswith("key_addons_devices|"), KeyAddonConfigState.configuring)
async def handle_addons_devices_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback.data.split("|", 2)
    if len(parts) < 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    new_devices = int(parts[2])
    data = await state.get_data()
    current_devices = data.get("addon_current_device_limit")
    selected_devices = data.get("addon_selected_device_limit")

    logger.debug(
        "[ADDONS] handle_addons_devices_choice: "
        f"tg_id={callback.from_user.id} email={data.get('addon_key_email')} "
        f"new_devices={new_devices} current_devices={current_devices} selected_devices={selected_devices}"
    )

    if selected_devices is not None and int(selected_devices) == new_devices:
        await callback.answer()
        return

    allow_downgrade = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))
    if not allow_downgrade and not is_not_downgrade(current_devices, new_devices):
        await callback.answer("Нельзя снижать лимит устройств, только увеличивать.", show_alert=True)
        return

    await state.update_data(addon_selected_device_limit=new_devices)
    await render_addons_screen(callback, state, session)


@router.callback_query(F.data.startswith("key_addons_traffic|"), KeyAddonConfigState.configuring)
async def handle_addons_traffic_choice(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback.data.split("|", 2)
    if len(parts) < 3:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    new_traffic_gb = int(parts[2])

    data = await state.get_data()
    current_traffic_gb = data.get("addon_current_traffic_gb")
    selected_traffic_gb = data.get("addon_selected_traffic_gb")

    logger.debug(
        "[ADDONS] handle_addons_traffic_choice: "
        f"tg_id={callback.from_user.id} email={data.get('addon_key_email')} "
        f"new_traffic_gb={new_traffic_gb} current_traffic_gb={current_traffic_gb} "
        f"selected_traffic_gb={selected_traffic_gb}"
    )

    if selected_traffic_gb is not None and int(selected_traffic_gb) == new_traffic_gb:
        await callback.answer()
        return

    allow_downgrade = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))
    if not allow_downgrade and not is_not_downgrade(current_traffic_gb, new_traffic_gb):
        await callback.answer("Нельзя снижать лимит трафика, только увеличивать.", show_alert=True)
        return

    await state.update_data(addon_selected_traffic_gb=new_traffic_gb)
    await render_addons_screen(callback, state, session)


@router.callback_query(F.data == "key_addons_downgrade", KeyAddonConfigState.configuring)
async def handle_addons_downgrade(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tg_id = callback.from_user.id
    data = await state.get_data()

    email = data.get("addon_key_email")
    tariff_id = data.get("addon_tariff_id")
    selected_devices = data.get("addon_selected_device_limit")
    selected_traffic_gb = data.get("addon_selected_traffic_gb")
    current_devices = data.get("addon_current_device_limit")
    current_traffic_gb = data.get("addon_current_traffic_gb")

    logger.info(
        "[ADDONS] handle_addons_downgrade: "
        f"tg_id={tg_id} email={email} tariff_id={tariff_id} "
        f"selected_devices={selected_devices} selected_traffic_gb={selected_traffic_gb} "
        f"current_devices={current_devices} current_traffic_gb={current_traffic_gb}"
    )

    if not email or not tariff_id:
        await callback.message.answer("❌ Данные для изменения подписки не найдены.")
        await state.clear()
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(f"[ADDONS] Тариф {tariff_id} не найден в handle_addons_downgrade")
        await callback.message.answer("❌ Тариф не найден.")
        await state.clear()
        return

    cfg = data.get("addon_tariff_config") or {}
    device_options = cfg.get("device_options") or []
    traffic_options = cfg.get("traffic_options_gb") or []
    has_device_option = bool(device_options)
    has_traffic_option = bool(traffic_options)
    has_device_choice = len(device_options) > 1
    has_traffic_choice = len(traffic_options) > 1

    total_price = calculate_config_price(
        tariff=tariff,
        selected_device_limit=int(selected_devices) if selected_devices is not None and has_device_option else None,
        selected_traffic_gb=int(selected_traffic_gb)
        if selected_traffic_gb is not None and has_traffic_option
        else None,
    )

    logger.debug(
        "[ADDONS] Downgrade price: "
        f"total_price={total_price} has_device_option={has_device_option} has_traffic_option={has_traffic_option}"
    )

    language_code = getattr(callback.from_user, "language_code", None)
    total_price_text = await format_for_user(session, tg_id, float(total_price), language_code)

    current_devices_label = format_devices_label(current_devices)
    current_traffic_label = format_traffic_label(current_traffic_gb)
    new_devices_label = format_devices_label(selected_devices)
    new_traffic_label = format_traffic_label(selected_traffic_gb)

    text = DOWNGRADE_WARNING_TEXT.format(
        tariff_name=tariff.get("name") or "подписка",
        current_devices_label=current_devices_label if has_device_choice else "по умолчанию",
        current_traffic_label=current_traffic_label if has_traffic_choice else "по умолчанию",
        new_devices_label=new_devices_label if has_device_choice else "по умолчанию",
        new_traffic_label=new_traffic_label if has_traffic_choice else "по умолчанию",
        total_price_text=total_price_text,
    )

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=DOWNGRADE_CONFIRM_BUTTON_TEXT,
            callback_data="key_addons_downgrade_apply",
        )
    )
    builder.row(InlineKeyboardButton(text=BACK, callback_data=f"key_addons|{email}"))

    await edit_or_send_message(
        target_message=callback.message,
        text=text,
        reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "key_addons_downgrade_apply", KeyAddonConfigState.configuring)
async def handle_addons_downgrade_apply(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()

    email = data.get("addon_key_email")
    tariff_id = data.get("addon_tariff_id")
    selected_devices = data.get("addon_selected_device_limit")
    selected_traffic_gb = data.get("addon_selected_traffic_gb")

    logger.info(
        "[ADDONS] handle_addons_downgrade_apply: "
        f"tg_id={callback.from_user.id} email={email} tariff_id={tariff_id} "
        f"selected_devices={selected_devices} selected_traffic_gb={selected_traffic_gb}"
    )

    if not email or not tariff_id:
        await callback.message.answer("❌ Данные для изменения подписки не найдены.")
        await state.clear()
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(f"[ADDONS] Тариф {tariff_id} не найден в handle_addons_downgrade_apply")
        await callback.message.answer("❌ Тариф не найден.")
        await state.clear()
        return

    cfg = data.get("addon_tariff_config") or {}
    device_options = cfg.get("device_options") or []
    traffic_options = cfg.get("traffic_options_gb") or []
    has_device_option = bool(device_options)
    has_traffic_option = bool(traffic_options)
    has_device_choice = len(device_options) > 1
    has_traffic_choice = len(traffic_options) > 1

    total_price = calculate_config_price(
        tariff=tariff,
        selected_device_limit=int(selected_devices) if selected_devices is not None and has_device_option else None,
        selected_traffic_gb=int(selected_traffic_gb)
        if selected_traffic_gb is not None and has_traffic_option
        else None,
    )

    logger.debug(
        "[ADDONS] Downgrade apply price: "
        f"total_price={total_price} has_device_choice={has_device_choice} has_traffic_choice={has_traffic_choice}"
    )

    try:
        await save_key_config_with_mode(
            session=session,
            email=email,
            selected_devices=selected_devices,
            selected_traffic_gb=selected_traffic_gb,
            total_price=int(total_price),
            has_device_choice=has_device_choice,
            has_traffic_choice=has_traffic_choice,
            config_mode="downgrade",
        )
        await session.commit()
    except Exception as error:
        logger.error(f"[ADDONS] Ошибка при сохранении будущих условий для {email}: {error}")
        await callback.message.answer("❌ Ошибка при сохранении новых условий. Попробуйте позже.")
        await state.clear()
        return

    await state.clear()
    await render_key_info(callback.message, session, email, "img/pic_view.jpg")
    await callback.answer(DOWNGRADE_SAVED_TEXT, show_alert=True)


@router.callback_query(F.data == "key_addons_confirm", KeyAddonConfigState.configuring)
async def handle_addons_confirm(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    from handlers.keys.operations import renew_key_in_cluster

    tg_id = callback.from_user.id
    data = await state.get_data()

    email = data.get("addon_key_email")
    tariff_id = data.get("addon_tariff_id")
    original_price = int(data.get("addon_original_price") or 0)
    selected_devices = data.get("addon_selected_device_limit")
    selected_traffic_gb = data.get("addon_selected_traffic_gb")
    current_devices = data.get("addon_current_device_limit")
    current_traffic_gb = data.get("addon_current_traffic_gb")

    logger.info(
        "[ADDONS] handle_addons_confirm: "
        f"tg_id={tg_id} email={email} tariff_id={tariff_id} original_price={original_price} "
        f"selected_devices={selected_devices} selected_traffic_gb={selected_traffic_gb} "
        f"current_devices={current_devices} current_traffic_gb={current_traffic_gb}"
    )

    if not email or not tariff_id:
        await callback.message.answer("❌ Данные для изменения подписки не найдены.")
        await state.clear()
        return

    record = await get_key_details(session, email)
    if not record:
        logger.warning(f"[ADDONS] Подписка {email} не найдена в handle_addons_confirm")
        await callback.message.answer("❌ Подписка не найдена.")
        await state.clear()
        return

    tariff = await get_tariff_by_id(session, int(tariff_id))
    if not tariff:
        logger.error(f"[ADDONS] Тариф {tariff_id} не найден в handle_addons_confirm")
        await callback.message.answer("❌ Тариф не найден.")
        await state.clear()
        return

    cfg = data.get("addon_tariff_config") or {}
    device_options = cfg.get("device_options") or []
    traffic_options = cfg.get("traffic_options_gb") or []
    has_device_option = bool(device_options)
    has_traffic_option = bool(traffic_options)
    has_device_choice = len(device_options) > 1
    has_traffic_choice = len(traffic_options) > 1

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
        "[ADDONS] Confirm prices: "
        f"base_price_for_current={base_price_for_current} total_price={total_price} "
        f"original_price={original_price} extra_price={extra_price} "
        f"has_device_option={has_device_option} has_traffic_option={has_traffic_option}"
    )

    allow_downgrade = bool(TARIFFS_CONFIG.get("ALLOW_DOWNGRADE", True))
    devices_downgrade = (
        allow_downgrade
        and has_device_choice
        and current_devices is not None
        and selected_devices is not None
        and not is_not_downgrade(current_devices, selected_devices)
    )
    traffic_downgrade = (
        allow_downgrade
        and has_traffic_choice
        and current_traffic_gb is not None
        and selected_traffic_gb is not None
        and not is_not_downgrade(current_traffic_gb, selected_traffic_gb)
    )

    logger.debug(
        f"[ADDONS] Confirm downgrade flags: devices_downgrade={devices_downgrade} traffic_downgrade={traffic_downgrade}"
    )

    if devices_downgrade or traffic_downgrade:
        await handle_addons_downgrade(callback, state, session)
        return

    if extra_price <= 0:
        logger.info(f"[ADDONS] extra_price <= 0, доплата не требуется, email={email}")
        await state.clear()
        await render_key_info(callback.message, session, email, "img/pic_view.jpg")
        await callback.answer(NO_EXTRA_PAYMENT_TEXT, show_alert=True)
        return

    balance = await get_balance(session, tg_id)
    logger.debug(f"[ADDONS] Balance check: tg_id={tg_id} balance={balance} extra_price={extra_price}")

    if balance < extra_price:
        required_amount = ceil(extra_price - balance)
        language_code = getattr(callback.from_user, "language_code", None)
        required_amount_text = await format_for_user(session, tg_id, float(required_amount), language_code)

        logger.info(
            "[ADDONS] Недостаточно средств: "
            f"balance={balance} extra_price={extra_price} required_amount={required_amount} "
            f"tg_id={tg_id} USE_NEW_PAYMENT_FLOW={USE_NEW_PAYMENT_FLOW}"
        )

        if USE_NEW_PAYMENT_FLOW:
            handled = await try_fast_payment_flow(
                callback,
                session,
                state,
                tg_id=tg_id,
                temp_key="waiting_for_addons_payment",
                temp_payload={
                    "email": email,
                    "tariff_id": int(tariff_id),
                    "original_price": original_price,
                    "selected_device_limit": selected_devices,
                    "selected_traffic_gb": selected_traffic_gb,
                    "current_device_limit": current_devices,
                    "current_traffic_gb": current_traffic_gb,
                    "required_amount": required_amount,
                },
                required_amount=required_amount,
            )
            logger.debug(f"[ADDONS] try_fast_payment_flow handled={handled} tg_id={tg_id} email={email}")
            if handled:
                return

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=PAYMENT, callback_data="pay"))
        await edit_or_send_message(
            target_message=callback.message,
            text=INSUFFICIENT_FUNDS_RENEWAL_MSG.format(required_amount=required_amount_text),
            reply_markup=builder.as_markup(),
        )
        return

    try:
        expiry_time = record["expiry_time"]
        client_id = record["client_id"]
        server_id = record["server_id"]

        selected_traffic_gb_for_effective = (
            int(selected_traffic_gb) if selected_traffic_gb is not None and has_traffic_option else 0
        )

        current_subgroup = None
        try:
            current_tariff_id = record.get("tariff_id")
            if current_tariff_id:
                current_tariff = await get_tariff_by_id(session, int(current_tariff_id))
                if current_tariff:
                    current_subgroup = current_tariff.get("subgroup_title")
        except Exception as error:
            logger.warning(f"[ADDONS] Не удалось определить текущую подгруппу: {error}")

        target_subgroup = tariff.get("subgroup_title")
        old_subgroup = current_subgroup

        device_limit_effective_new, traffic_limit_bytes_effective_new = await get_effective_limits_for_key(
            session=session,
            tariff_id=int(tariff_id),
            selected_device_limit=int(selected_devices) if selected_devices is not None and has_device_option else None,
            selected_traffic_gb=selected_traffic_gb_for_effective,
        )
        traffic_limit_gb_effective = (
            int(traffic_limit_bytes_effective_new / GB) if traffic_limit_bytes_effective_new else 0
        )
        total_gb = traffic_limit_gb_effective
        hwid_device_limit_to_set = device_limit_effective_new

        logger.debug(
            "[ADDONS] renew_key_in_cluster params: "
            f"server_id={server_id} email={email} client_id={client_id} total_gb={total_gb} "
            f"hwid_device_limit_to_set={hwid_device_limit_to_set} target_subgroup={target_subgroup} "
            f"old_subgroup={old_subgroup}"
        )

        await renew_key_in_cluster(
            cluster_id=server_id,
            email=email,
            client_id=client_id,
            new_expiry_time=expiry_time,
            total_gb=total_gb,
            session=session,
            hwid_device_limit=hwid_device_limit_to_set,
            reset_traffic=False,
            target_subgroup=target_subgroup,
            old_subgroup=old_subgroup,
            plan=int(tariff_id),
        )

        await save_key_config_with_mode(
            session=session,
            email=email,
            selected_devices=selected_devices,
            selected_traffic_gb=selected_traffic_gb,
            total_price=int(total_price),
            has_device_choice=has_device_choice,
            has_traffic_choice=has_traffic_choice,
            config_mode="addon",
        )

        await update_balance(session, tg_id, -extra_price)
        await session.commit()

        logger.info(
            "[ADDONS] Успешное применение расширения: "
            f"tg_id={tg_id} email={email} total_price={total_price} extra_price={extra_price}"
        )

        await state.clear()
        await render_key_info(callback.message, session, email, "img/pic_view.jpg")
        await callback.answer(ADDONS_APPLIED_TEXT, show_alert=True)

    except Exception as error:
        logger.error(f"[ADDONS] Ошибка при применении расширения подписки для {email}: {error}")
        await callback.message.answer("❌ Ошибка при обновлении подписки. Попробуйте позже.")
        await state.clear()
