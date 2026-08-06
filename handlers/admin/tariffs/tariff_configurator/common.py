from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings.tariffs_config import normalize_tariff_config
from database.models import Tariff
from filters.admin import IsAdminFilter

from ...panel.headers import card, menu_text, quote, section
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
                    text="💰 Шаг за устройство",
                    callback_data=f"cfg_edit_device_step|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Доплаты за устройства",
                    callback_data=f"cfg_edit_device_over|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💰 Шаг за 1 ГБ",
                    callback_data=f"cfg_edit_traffic_step|{tariff_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Доплаты за трафик",
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

    lines: list[str] = [
        menu_text(
            "Доплаты за устройства",
            "Нажмите на вариант, чтобы задать доплату.",
            quote(f"Базовая цена тарифа: <b>{base_price}₽</b>"),
            quote(
                "<code>0</code> среди вариантов — безлимит по устройствам.",
                "Доплата <code>0</code> возвращает расчёт по базовому шагу.",
            ),
        ),
        "",
        "Текущие значения:",
    ]

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
            text="🧹 Сбросить свои доплаты",
            callback_data=f"cfg_dev_over_clear|{tariff_id}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К конфигуратору",
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

    lines: list[str] = [
        menu_text(
            "Доплаты за трафик",
            "Нажмите на вариант, чтобы задать доплату.",
            quote(f"Базовая цена тарифа: <b>{base_price}₽</b>"),
            quote(
                "<code>0</code> среди вариантов — безлимитный трафик.",
                "Доплата <code>0</code> возвращает расчёт по базовому шагу.",
            ),
        ),
        "",
        "Текущие значения:",
    ]

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
            text="🧹 Сбросить свои доплаты",
            callback_data=f"cfg_trf_over_clear|{tariff_id}",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ К конфигуратору",
            callback_data=f"edit_config|{tariff_id}",
        )
    ])

    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, markup


def build_config_summary_text(tariff: Tariff) -> str:
    """Возвращает экран конфигуратора тарифа."""
    cfg = normalize_tariff_config(tariff.to_dict())

    base_devices = tariff.device_limit if tariff.device_limit is not None else "—"
    base_traffic = "безлимит" if tariff.traffic_limit is None else f"{tariff.traffic_limit} ГБ"

    device_options = cfg.get("device_options") or []
    traffic_options_gb = cfg.get("traffic_options_gb")

    if device_options:
        devices_choice = ", ".join("безлимит" if d == 0 else str(d) for d in device_options)
    else:
        devices_choice = f"выкл, {base_devices}"

    if traffic_options_gb is None:
        traffic_choice = "выкл"
    else:
        traffic_choice = ", ".join("безлимит" if g == 0 else f"{g} ГБ" for g in traffic_options_gb)

    device_step = getattr(tariff, "device_step_rub", None) or 0
    traffic_step = getattr(tariff, "traffic_step_rub", None) or 0
    device_overrides = getattr(tariff, "device_overrides", None) or {}
    traffic_overrides = getattr(tariff, "traffic_overrides", None) or {}

    device_rows = [f"Шаг: {device_step} ₽ за устройство"]
    for key, value in sorted(device_overrides.items(), key=lambda item: int(item[0])):
        label = "безлимит" if int(key) == 0 else f"{int(key)} шт"
        device_rows.append(f"{label}: {int(value)} ₽")

    traffic_rows = [f"Шаг: {traffic_step} ₽ за 1 ГБ"]
    for key, value in sorted(traffic_overrides.items(), key=lambda item: int(item[0])):
        label = "безлимит" if int(key) == 0 else f"{int(key)} ГБ"
        traffic_rows.append(f"{label}: {int(value)} ₽")

    body = card(
        section(
            "🎯 База",
            f"Срок: {tariff.duration_days} дн",
            f"Устройства: {base_devices}",
            f"Трафик: {base_traffic}",
            f"Цена: {tariff.price_rub or 0} ₽",
        ),
        section(
            "⚙️ Выбор клиента",
            "Срок: фиксированный",
            f"Устройства: {devices_choice}",
            f"Трафик: {traffic_choice}",
        ),
        section("📱 Доплата за устройства", *device_rows),
        section("📦 Доплата за трафик", *traffic_rows),
    )

    status = "включён" if getattr(tariff, "configurable", False) else "выключен"
    return menu_text("Конфигуратор", f"<b>{tariff.name}</b>", quote(f"Конфигуратор: {status}"), body)


@router.callback_query(F.data.startswith("edit_config|"), IsAdminFilter())
async def open_config_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text(menu_text("Конфигуратор", "❌ Тариф не найден."))
        return

    await state.set_state(TariffConfigState.choosing_section)
    await state.update_data(tariff_id=tariff_id)

    text = build_config_summary_text(tariff)
    await callback.message.edit_text(text=text, reply_markup=build_config_menu_kb(tariff_id))
