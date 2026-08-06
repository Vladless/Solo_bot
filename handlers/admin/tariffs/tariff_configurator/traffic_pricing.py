from datetime import datetime

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from database.models import Tariff
from filters.admin import IsAdminFilter

from ...panel.headers import menu_text, quote, section
from .. import router
from .common import (
    TariffConfigState,
    build_cancel_config_kb,
    build_config_menu_kb,
    build_config_summary_text,
    build_traffic_overrides_screen,
    calculate_traffic_formula_extra,
)


@router.callback_query(F.data.startswith("cfg_edit_traffic_step|"), TariffConfigState.choosing_section, IsAdminFilter())
async def ask_traffic_step(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("|")[1])
    await state.set_state(TariffConfigState.entering_traffic_step)
    await state.update_data(tariff_id=tariff_id)

    text = menu_text(
        "Шаг за трафик",
        "Цена в рублях за 1 ГБ сверх базового лимита.",
        quote("Например: <code>5</code>"),
        quote("<code>0</code> выключает автодоплату за трафик."),
    )
    await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))


@router.message(TariffConfigState.entering_traffic_step, IsAdminFilter())
async def save_traffic_step(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff_id = data["tariff_id"]

    try:
        price = int(message.text.strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            menu_text(
                "Конфигуратор",
                "❌ Нужно число от нуля.",
                markup=build_cancel_config_kb(tariff_id),
            ),
            reply_markup=build_cancel_config_kb(tariff_id),
        )
        return

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await message.answer(menu_text("Конфигуратор", "❌ Тариф не найден."))
        await state.clear()
        return

    tariff.traffic_step_rub = price
    tariff.updated_at = datetime.utcnow()

    await state.set_state(TariffConfigState.choosing_section)

    text = build_config_summary_text(tariff)
    await message.answer(text=text, reply_markup=build_config_menu_kb(tariff_id))


@router.callback_query(F.data.startswith("cfg_edit_traffic_over|"), TariffConfigState.choosing_section, IsAdminFilter())
async def open_traffic_overrides_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text(menu_text("Конфигуратор", "❌ Тариф не найден."))
        return

    await state.set_state(TariffConfigState.entering_traffic_overrides)
    await state.update_data(tariff_id=tariff_id, traffic_override_gb=None)

    text, markup = build_traffic_overrides_screen(tariff)
    await callback.message.edit_text(text=menu_text("Конфигуратор", text, markup=markup), reply_markup=markup)


@router.callback_query(
    F.data.startswith("cfg_trf_over_item|"), TariffConfigState.entering_traffic_overrides, IsAdminFilter()
)
async def choose_traffic_override_option(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    parts = callback.data.split("|")
    tariff_id = int(parts[1])
    gb_value = int(parts[2])

    await state.update_data(tariff_id=tariff_id, traffic_override_gb=gb_value)

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text(menu_text("Конфигуратор", "❌ Тариф не найден."))
        await state.clear()
        return

    overrides = getattr(tariff, "traffic_overrides", None) or {}
    key = str(gb_value)
    formula_extra = calculate_traffic_formula_extra(tariff, gb_value)
    override_extra = overrides.get(key)
    if override_extra is not None:
        effective_extra = int(override_extra)
        note = "индивидуальная доплата"
    else:
        effective_extra = formula_extra
        note = "доплата по базовому шагу"

    if gb_value == 0:
        label = "безлимитный трафик"
    else:
        label = f"лимит {gb_value} ГБ"

    text = menu_text(
        "Доплата за вариант",
        f"<b>{label}</b>",
        section("💰 Сейчас", f"Доплата: {effective_extra} ₽", f"Расчёт: {note}"),
        quote("Пришлите новую доплату в рублях. Ноль вернёт расчёт по базовому шагу."),
    )
    await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))


@router.callback_query(
    F.data.startswith("cfg_trf_over_clear|"), TariffConfigState.entering_traffic_overrides, IsAdminFilter()
)
async def clear_traffic_overrides(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text(menu_text("Конфигуратор", "❌ Тариф не найден."))
        await state.clear()
        return

    tariff.traffic_overrides = None
    tariff.updated_at = datetime.utcnow()

    text, markup = build_traffic_overrides_screen(tariff)

    try:
        await callback.message.edit_text(text=menu_text("Конфигуратор", text, markup=markup), reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


@router.message(TariffConfigState.entering_traffic_overrides, IsAdminFilter())
async def save_traffic_override_price(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    gb_value = data.get("traffic_override_gb")

    if not tariff_id or gb_value is None:
        await message.answer(menu_text("Конфигуратор", "Сначала выберите вариант лимита трафика из списка."))
        return

    try:
        extra_price = int(message.text.strip())
        if extra_price < 0:
            raise ValueError
    except ValueError:
        await message.answer(
            menu_text("Конфигуратор", "❌ Нужно число от нуля."),
            reply_markup=build_cancel_config_kb(int(tariff_id)),
        )
        return

    result = await session.execute(select(Tariff).where(Tariff.id == int(tariff_id)))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await message.answer(menu_text("Конфигуратор", "❌ Тариф не найден."))
        await state.clear()
        return

    existing_overrides = tariff.traffic_overrides
    overrides = dict(existing_overrides) if existing_overrides else {}
    key = str(int(gb_value))

    if extra_price == 0:
        overrides.pop(key, None)
    else:
        overrides[key] = extra_price

    tariff.traffic_overrides = overrides if overrides else None
    attributes.flag_modified(tariff, "traffic_overrides")
    tariff.updated_at = datetime.utcnow()

    await state.update_data(traffic_override_gb=None)

    text, markup = build_traffic_overrides_screen(tariff)
    await message.answer(text=menu_text("Конфигуратор", text, markup=markup), reply_markup=markup)
