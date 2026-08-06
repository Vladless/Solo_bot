import re

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Tariff
from filters.admin import IsAdminFilter

from ...panel.headers import menu_text, quote
from .. import router
from .common import (
    TariffConfigState,
    build_cancel_config_kb,
    build_config_menu_kb,
    build_config_summary_text,
)


@router.callback_query(F.data.startswith("cfg_edit_devices|"), TariffConfigState.choosing_section, IsAdminFilter())
async def ask_devices_config(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("|")[1])
    await state.set_state(TariffConfigState.entering_devices)
    await state.update_data(tariff_id=tariff_id)

    text = menu_text(
        "Варианты устройств",
        "Пришлите список через пробел или запятую.",
        quote("Например: <code>1 3 5</code>"),
        quote(
            "<code>0</code> в списке — вариант «безлимит».",
            "Один только <code>0</code> — выбор устройств отключён, остаётся базовый лимит тарифа.",
        ),
    )
    await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))


@router.message(TariffConfigState.entering_devices, IsAdminFilter())
async def save_devices_config(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff_id = data["tariff_id"]
    raw_text = message.text.strip()

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await message.answer(menu_text("Конфигуратор", "❌ Тариф не найден."))
        await state.clear()
        return

    if raw_text == "0":
        tariff.device_options = None
    else:
        try:
            parts = [p for p in re.split(r"[,\s]+", raw_text) if p.strip()]
            if not parts:
                raise ValueError
            values: list[int] = []
            for part in parts:
                v = int(part)
                if v < 0:
                    raise ValueError
                values.append(v)
            values = sorted(set(values))
            tariff.device_options = values
        except Exception:
            await message.answer(
                menu_text(
                    "Некорректные значения",
                    "Нужны числа 0 и больше через пробел или запятую.",
                    quote("Например: <code>1 3 5</code>"),
                    markup=build_cancel_config_kb(tariff_id),
                ),
                reply_markup=build_cancel_config_kb(tariff_id),
            )
            return

    tariff.updated_at = datetime.utcnow()

    await state.set_state(TariffConfigState.choosing_section)

    text = build_config_summary_text(tariff)
    await message.answer(text=text, reply_markup=build_config_menu_kb(tariff_id))


@router.callback_query(F.data.startswith("cfg_edit_traffic|"), TariffConfigState.choosing_section, IsAdminFilter())
async def ask_traffic_config(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("|")[1])
    await state.set_state(TariffConfigState.entering_traffic)
    await state.update_data(tariff_id=tariff_id)

    text = menu_text(
        "Варианты трафика",
        "Пришлите список ГБ через пробел или запятую.",
        quote("Например: <code>100 200 500</code>"),
        quote(
            "<code>0</code> в списке — вариант «безлимит».",
            "Один только <code>0</code> — выбор трафика отключён, остаётся базовый лимит тарифа.",
        ),
    )
    await callback.message.edit_text(text=text, reply_markup=build_cancel_config_kb(tariff_id))


@router.message(TariffConfigState.entering_traffic, IsAdminFilter())
async def save_traffic_config(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff_id = data["tariff_id"]
    raw_text = message.text.strip()

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await message.answer(menu_text("Конфигуратор", "❌ Тариф не найден."))
        await state.clear()
        return

    if raw_text == "0":
        tariff.traffic_options_gb = None
    else:
        try:
            parts = [p for p in re.split(r"[,\s]+", raw_text) if p.strip()]
            if not parts:
                raise ValueError
            values: list[int] = []
            for part in parts:
                v = int(part)
                if v < 0:
                    raise ValueError
                values.append(v)
            values = sorted(set(values))
            tariff.traffic_options_gb = values
        except Exception:
            await message.answer(
                menu_text(
                    "Некорректные значения",
                    "Нужны числа 0 и больше через пробел или запятую.",
                    quote("Например: <code>100 200 500</code>"),
                    markup=build_cancel_config_kb(tariff_id),
                ),
                reply_markup=build_cancel_config_kb(tariff_id),
            )
            return

    tariff.updated_at = datetime.utcnow()

    await state.set_state(TariffConfigState.choosing_section)

    text = build_config_summary_text(tariff)
    await message.answer(text=text, reply_markup=build_config_menu_kb(tariff_id))
