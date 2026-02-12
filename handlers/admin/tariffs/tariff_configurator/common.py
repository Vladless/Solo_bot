from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.tariffs_config import normalize_tariff_config
from database.models import Tariff
from filters.admin import IsAdminFilter

from .. import router
from ..keyboard import AdminTariffCallback


class TariffConfigState(StatesGroup):
    choosing_section = State()
    entering_devices = State()
    entering_traffic = State()
    entering_device_step = State()
    entering_device_overrides = State()
    entering_traffic_step = State()
    entering_traffic_overrides = State()


def build_config_menu_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Варианты устройств",
                    callback_data=f"cfg_edit_devices|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Варианты трафика",
                    callback_data=f"cfg_edit_traffic|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Шаг доплаты за устройства",
                    callback_data=f"cfg_edit_device_step|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Доплаты по вариантам устройств",
                    callback_data=f"cfg_edit_device_over|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Шаг доплаты за трафик (ГБ)",
                    callback_data=f"cfg_edit_traffic_step|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Доплаты по вариантам трафика",
                    callback_data=f"cfg_edit_traffic_over|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к тарифу",
                    callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack(),
                )
            ],
        ]
    )


def build_cancel_config_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=f"edit_config|{tariff_id}",
                )
            ]
        ]
    )


def calculate_device_formula_extra(tariff: Tariff, devices: int) -> int:
    base_devices = tariff.device_limit
    step = getattr(tariff, "device_step_rub", None) or 0
    if base_devices is None or devices <= base_devices:
        return 0
    return (devices - base_devices) * step


def calculate_traffic_formula_extra(tariff: Tariff, gb_value: int) -> int:
    base_traffic = tariff.traffic_limit
    step = getattr(tariff, "traffic_step_rub", None) or 0
    if gb_value == 0:
        return 0
    if base_traffic is None or gb_value <= base_traffic:
        return 0
    return (gb_value - base_traffic) * step


def build_device_overrides_screen(tariff: Tariff) -> tuple[str, InlineKeyboardMarkup]:
    tariff_id = tariff.id
    base_price = int(tariff.price_rub or 0)
    device_options = tariff.device_options or []
    overrides = getattr(tariff, "device_overrides", None) or {}

    lines: list[str] = []
    lines.append("📊 Доплаты по вариантам устройств.")
    lines.append("")
    lines.append(f"Базовая цена тарифа: <b>{base_price}₽</b>")
    lines.append("Ниже показаны варианты устройств и текущая доплата.")
    lines.append("Значение <code>0</code> можно использовать как безлимит по устройствам.")
    lines.append("Нажмите на вариант, чтобы задать доплату в рублях.")
    lines.append("Отправьте <code>0</code>, чтобы вернуть расчёт по базовому шагу.")
    lines.append("")
    lines.append("Текущие значения:")

    for devices in sorted(device_options):
        key = str(devices)
        formula_extra = calculate_device_formula_extra(tariff, devices)
        override_extra = overrides.get(key)
        if override_extra is not None:
            effective_extra = int(override_extra)
            status = " (индивидуальная доплата)"
        else:
            effective_extra = formula_extra
            status = ""
        if devices == 0:
            label = "безлимит устройств"
        else:
            label = f"{devices} устр."
        lines.append(f"• {label}: доплата {effective_extra}₽{status}")

    text = "\n".join(lines)

    rows: list[list[InlineKeyboardButton]] = []
    for devices in sorted(device_options):
        key = str(devices)
        formula_extra = calculate_device_formula_extra(tariff, devices)
        override_extra = overrides.get(key)
        if override_extra is not None:
            effective_extra = int(override_extra)
            status = "★"
        else:
            effective_extra = formula_extra
            status = ""
        if devices == 0:
            name = "безлимит устройств"
        else:
            name = f"{devices} устр."
        if effective_extra > 0:
            label = f"{status} {name} — доплата +{effective_extra}₽"
        else:
            label = f"{status} {name} — без доплаты"
        label = label.strip()
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"cfg_dev_over_item|{tariff_id}|{devices}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="🧹 Сбросить все индивидуальные доплаты",
            callback_data=f"cfg_dev_over_clear|{tariff_id}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад к конфигуратору",
            callback_data=f"edit_config|{tariff_id}",
        )
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, markup


def build_traffic_overrides_screen(tariff: Tariff) -> tuple[str, InlineKeyboardMarkup]:
    tariff_id = tariff.id
    base_price = int(tariff.price_rub or 0)
    traffic_options = tariff.traffic_options_gb or []
    overrides = getattr(tariff, "traffic_overrides", None) or {}

    all_options = sorted(set(traffic_options + [0]))

    lines: list[str] = []
    lines.append("📊 Доплаты по вариантам трафика.")
    lines.append("")
    lines.append(f"Базовая цена тарифа: <b>{base_price}₽</b>")
    lines.append("Ниже показаны варианты лимитов и текущая доплата.")
    lines.append("Значение <code>0</code> — безлимитный трафик.")
    lines.append("Нажмите на вариант, чтобы задать доплату в рублях.")
    lines.append("Отправьте <code>0</code>, чтобы вернуть расчёт по базовому шагу.")
    lines.append("")
    lines.append("Текущие значения:")

    for gb in all_options:
        key = str(gb)
        formula_extra = calculate_traffic_formula_extra(tariff, gb)
        override_extra = overrides.get(key)
        if override_extra is not None:
            effective_extra = int(override_extra)
            status = " (индивидуальная доплата)"
        else:
            effective_extra = formula_extra
            status = ""
        if gb == 0:
            label = "безлимит"
        else:
            label = f"{gb} ГБ"
        lines.append(f"• {label}: доплата {effective_extra}₽{status}")

    text = "\n".join(lines)

    rows: list[list[InlineKeyboardButton]] = []
    for gb in all_options:
        key = str(gb)
        formula_extra = calculate_traffic_formula_extra(tariff, gb)
        override_extra = overrides.get(key)
        if override_extra is not None:
            effective_extra = int(override_extra)
            status = "★"
        else:
            effective_extra = formula_extra
            status = ""
        if gb == 0:
            name = "безлимит"
        else:
            name = f"{gb} ГБ"
        if effective_extra > 0:
            label = f"{status} {name} — доплата +{effective_extra}₽"
        else:
            label = f"{status} {name} — без доплаты"
        label = label.strip()
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"cfg_trf_over_item|{tariff_id}|{gb}",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="🧹 Сбросить все индивидуальные доплаты",
            callback_data=f"cfg_trf_over_clear|{tariff_id}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад к конфигуратору",
            callback_data=f"edit_config|{tariff_id}",
        )
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, markup


def build_config_summary_text(tariff: Tariff) -> str:
    cfg = normalize_tariff_config(tariff.to_dict())
    configurable_text = "включен" if getattr(tariff, "configurable", False) else "выключен"

    base_duration = tariff.duration_days
    base_devices = tariff.device_limit if tariff.device_limit is not None else "—"
    if tariff.traffic_limit is None:
        base_traffic_text = "безлимит"
    else:
        base_traffic_text = f"{tariff.traffic_limit} ГБ"
    base_price = tariff.price_rub or 0

    device_options = cfg.get("device_options") or []
    traffic_options_gb = cfg.get("traffic_options_gb")

    duration_line = f"📅 Длительность: фиксированная, {base_duration} дн."

    if device_options:
        devices_parts = []
        for d in device_options:
            if d == 0:
                devices_parts.append("безлимит")
            else:
                devices_parts.append(str(d))
        devices_str = ", ".join(devices_parts)
        devices_line = f"📱 Устройства: варианты — {devices_str}"
    else:
        devices_line = f"📱 Устройства: выбор отключён, по умолчанию {base_devices}"

    if traffic_options_gb is None:
        traffic_line = "📦 Трафик: выбор трафика отключён"
    else:
        traffic_parts = []
        for g in traffic_options_gb:
            if g == 0:
                traffic_parts.append("безлимит")
            else:
                traffic_parts.append(f"{g} ГБ")
        traffic_str = ", ".join(traffic_parts)
        traffic_line = f"📦 Трафик: варианты — {traffic_str}"

    device_step = getattr(tariff, "device_step_rub", None) or 0
    traffic_step = getattr(tariff, "traffic_step_rub", None) or 0

    device_overrides = getattr(tariff, "device_overrides", None) or {}
    traffic_overrides = getattr(tariff, "traffic_overrides", None) or {}

    base_block = (
        "<blockquote>"
        "🎯 База тарифа:\n"
        f"• Длительность: <b>{base_duration} дней</b>\n"
        f"• Устройства: <b>{base_devices}</b>\n"
        f"• Трафик: <b>{base_traffic_text}</b>\n"
        f"• Цена: <b>{base_price}₽</b>\n"
        "</blockquote>\n"
    )

    config_block = f"<blockquote>\n{duration_line}\n{devices_line}\n{traffic_line}\n</blockquote>\n"

    device_step_line = (
        f"💰 Устройства, базовый шаг: {device_step}₽ за каждое устройство сверх базового лимита ({base_devices})"
    )

    if device_overrides:
        parts = []
        for k, v in sorted(device_overrides.items(), key=lambda x: int(x[0])):
            devices_count = int(k)
            extra = int(v)
            if devices_count == 0:
                label = "безлимит устройств"
            else:
                label = f"{devices_count} устройств"
            parts.append(f"при {label}: индивидуальная доплата {extra}₽")
        device_over_line = "📊 Устройства, индивидуальные доплаты:\n" + "\n".join(f"• {p}" for p in parts)
    else:
        device_over_line = "📊 Устройства, индивидуальные доплаты: не заданы"

    device_block = f"<blockquote>{device_step_line}\n{device_over_line}\n</blockquote>\n"

    traffic_step_line = f"💰 Трафик, базовый шаг: {traffic_step}₽ за 1 ГБ сверх базового лимита ({base_traffic_text})"

    if traffic_overrides:
        parts = []
        for k, v in sorted(traffic_overrides.items(), key=lambda x: int(x[0])):
            gb_value = int(k)
            extra = int(v)
            if gb_value == 0:
                label = "безлимитный трафик"
            else:
                label = f"лимит {gb_value} ГБ"
            parts.append(f"при {label}: индивидуальная доплата {extra}₽")
        traffic_over_line = "📊 Трафик, индивидуальные доплаты:\n" + "\n".join(f"• {p}" for p in parts)
    else:
        traffic_over_line = "📊 Трафик, индивидуальные доплаты: не заданы"

    traffic_block = f"<blockquote>\n{traffic_step_line}\n{traffic_over_line}\n</blockquote>"

    return (
        f"<b>⚙️ Конфигуратор тарифа: {tariff.name}</b>\n\n"
        f"Статус: <b>{configurable_text}</b>\n\n"
        f"{base_block}"
        f"{config_block}"
        f"{device_block}"
        f"{traffic_block}"
    )


@router.callback_query(F.data.startswith("edit_config|"), IsAdminFilter())
async def open_config_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        return

    await state.set_state(TariffConfigState.choosing_section)
    await state.update_data(tariff_id=tariff_id)

    text = build_config_summary_text(tariff)
    await callback.message.edit_text(text=text, reply_markup=build_config_menu_kb(tariff_id))
