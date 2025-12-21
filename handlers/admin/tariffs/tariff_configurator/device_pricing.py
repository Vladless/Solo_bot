from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from database.models import Tariff
from filters.admin import IsAdminFilter

from .. import router
from .common import (
    TariffConfigState,
    build_cancel_config_kb,
    build_config_menu_kb,
    build_config_summary_text,
    build_device_overrides_screen,
    calculate_device_formula_extra,
)


@router.callback_query(F.data.startswith("cfg_edit_device_step|"), TariffConfigState.choosing_section, IsAdminFilter())
async def ask_device_step(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("|")[1])
    await state.set_state(TariffConfigState.entering_device_step)
    await state.update_data(tariff_id=tariff_id)

    text = (
        "💰 Базовый шаг доплаты за устройства.\n\n"
        "Введите цену в рублях за КАЖДОЕ устройство сверх базового лимита.\n"
        "Например: <code>50</code>\n\n"
        "Чтобы выключить автоматическую доплату за устройства, отправьте <code>0</code>."
    )
    await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))


@router.message(TariffConfigState.entering_device_step, IsAdminFilter())
async def save_device_step(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff_id = data["tariff_id"]

    try:
        price = int(message.text.strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Некорректное значение. Введите целое число 0 или больше.",
            reply_markup=build_cancel_config_kb(tariff_id),
        )
        return

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await message.answer("❌ Тариф не найден.")
        await state.clear()
        return

    tariff.device_step_rub = price
    tariff.updated_at = datetime.utcnow()
    await session.commit()

    await state.set_state(TariffConfigState.choosing_section)

    text = build_config_summary_text(tariff)
    await message.answer(text=text, reply_markup=build_config_menu_kb(tariff_id))


@router.callback_query(F.data.startswith("cfg_edit_device_over|"), TariffConfigState.choosing_section, IsAdminFilter())
async def open_device_overrides_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        return

    device_options = tariff.device_options or []
    if not device_options:
        await callback.message.edit_text(
            "Сначала настройте варианты устройств (кнопка «📱 Варианты устройств»).",
            reply_markup=build_config_menu_kb(tariff_id),
        )
        return

    await state.set_state(TariffConfigState.entering_device_overrides)
    await state.update_data(tariff_id=tariff_id, devices_override=None)

    text, markup = build_device_overrides_screen(tariff)
    await callback.message.edit_text(text=text, reply_markup=markup)


@router.callback_query(
    F.data.startswith("cfg_dev_over_item|"), TariffConfigState.entering_device_overrides, IsAdminFilter()
)
async def choose_device_override_option(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback.data.split("|")
    tariff_id = int(parts[1])
    devices = int(parts[2])

    await state.update_data(tariff_id=tariff_id, devices_override=devices)

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        await state.clear()
        return

    overrides = getattr(tariff, "device_overrides", None) or {}
    key = str(devices)
    formula_extra = calculate_device_formula_extra(tariff, devices)
    override_extra = overrides.get(key)
    if override_extra is not None:
        effective_extra = int(override_extra)
        note = "индивидуальная доплата"
    else:
        effective_extra = formula_extra
        note = "доплата по базовому шагу"

    if devices == 0:
        label = "безлимитное количество устройств"
    else:
        label = f"{devices} устройств"

    text = (
        f"📊 {label}.\n\n"
        f"Текущая доплата для этого варианта: <b>{effective_extra}₽</b> ({note}).\n\n"
        "Введите новую <u>доплату за устройства</u> для этого варианта в рублях.\n"
        "Отправьте <code>0</code>, чтобы вернуть расчёт по базовому шагу."
    )
    await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))


@router.callback_query(
    F.data.startswith("cfg_dev_over_clear|"), TariffConfigState.entering_device_overrides, IsAdminFilter()
)
async def clear_device_overrides(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        await state.clear()
        return

    tariff.device_overrides = None
    tariff.updated_at = datetime.utcnow()
    await session.commit()

    text, markup = build_device_overrides_screen(tariff)
    await callback.message.edit_text(text=text, reply_markup=markup)


@router.message(TariffConfigState.entering_device_overrides, IsAdminFilter())
async def save_device_override_price(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    devices = data.get("devices_override")

    if not tariff_id or devices is None:
        await message.answer("Сначала выберите вариант устройств из списка.")
        return

    try:
        extra_price = int(message.text.strip())
        if extra_price < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Некорректное значение. Введите целое число 0 или больше.",
            reply_markup=build_cancel_config_kb(int(tariff_id)),
        )
        return

    result = await session.execute(select(Tariff).where(Tariff.id == int(tariff_id)))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await message.answer("❌ Тариф не найден.")
        await state.clear()
        return

    existing_overrides = tariff.device_overrides
    overrides = dict(existing_overrides) if existing_overrides else {}
    key = str(int(devices))

    if extra_price == 0:
        overrides.pop(key, None)
    else:
        overrides[key] = extra_price

    tariff.device_overrides = overrides if overrides else None
    attributes.flag_modified(tariff, "device_overrides")
    tariff.updated_at = datetime.utcnow()
    await session.commit()

    await state.update_data(devices_override=None)

    text, markup = build_device_overrides_screen(tariff)
    await message.answer(text=text, reply_markup=markup)
