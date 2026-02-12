from collections import defaultdict
from datetime import datetime

import pytz

from aiogram import F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Tariff
from database.tariffs import (
    get_tariffs,
    move_tariff_down as db_move_tariff_down,
    move_tariff_up as db_move_tariff_up,
)
from filters.admin import IsAdminFilter

from . import router
from .keyboard import (
    AdminTariffCallback,
    build_tariff_arrangement_groups_kb,
    build_tariffs_arrangement_kb,
)
from .tariff_utils import render_tariff_card


@router.callback_query(AdminTariffCallback.filter(F.action == "arrange"), IsAdminFilter())
async def show_tariff_arrangement_menu(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(
        select(Tariff.group_code).where(Tariff.group_code.isnot(None)).distinct().order_by(Tariff.group_code)
    )
    groups = [row[0] for row in result.fetchall()]

    if not groups:
        await callback.message.edit_text("❌ Нет доступных групп тарифов.")
        return

    await callback.message.edit_text(
        "🔢 <b>Управление расположением тарифов</b>\n\n"
        "📋 <b>Как это работает:</b>\n"
        "• Тарифы отображаются в порядке их расположения\n"
        "• Меньший номер = выше в списке\n"
        "• Новые тарифы добавляются в конец списка\n"
        "• ⬆️ поднимает тариф выше (номер уменьшается)\n"
        "• ⬇️ опускает тариф ниже (номер увеличивается)\n"
        "• Подгруппы сортируются по общей сумме тарифов внутри\n\n"
        "Выберите группу для управления расположением:",
        reply_markup=build_tariff_arrangement_groups_kb(groups),
    )


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("arrange_group|")), IsAdminFilter())
async def show_tariffs_arrangement(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    group_code = callback_data.action.split("|")[1]

    tariffs_data = await get_tariffs(session, group_code=group_code, with_subgroup_weights=True)
    tariffs = [t for t in tariffs_data["tariffs"] if t.get("is_active")]
    subgroup_weights = tariffs_data["subgroup_weights"]

    if not tariffs:
        await callback.message.edit_text("❌ В этой группе пока нет активных тарифов.")
        return

    grouped_tariffs = defaultdict(list)
    for t in tariffs:
        grouped_tariffs[t.get("subgroup_title")].append(t)

    sorted_subgroups = sorted(
        [k for k in grouped_tariffs if k],
        key=lambda x: (subgroup_weights.get(x, 999999), x),
    )

    moscow_tz = pytz.timezone("Europe/Moscow")
    now = datetime.now(moscow_tz)
    current_time = now.strftime("%d.%m.%y %H:%M:%S МСК")

    text = f"🔢 <b>Итоговая сортировка тарифов в группе: {group_code}</b>\n\n"

    if grouped_tariffs.get(None):
        text += "<b>📋 Основные тарифы:</b>\n"
        for t in grouped_tariffs[None]:
            sort_order = t.get("sort_order", 1)
            text += f"• {t.get('name')} <code>[позиция: {sort_order}]</code>\n"
        text += "\n"

    if sorted_subgroups:
        text += "<b>📁 Подгруппы:</b>\n"
        for subgroup in sorted_subgroups:
            subgroup_weight = subgroup_weights.get(subgroup, 999999)
            text += f"• <b>{subgroup}</b> <code>[вес группы: {subgroup_weight}]</code>\n"
            for t in grouped_tariffs[subgroup]:
                sort_order = t.get("sort_order", 1)
                text += f"  └ {t.get('name')} <code>[позиция: {sort_order}]</code>\n"
            text += "\n"

    text += f"\n{current_time}"

    await callback.message.edit_text(
        text,
        reply_markup=build_tariffs_arrangement_kb(group_code, tariffs),
    )


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("move_up|")), IsAdminFilter())
async def move_tariff_up(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    tariff_id = int(callback_data.action.split("|")[1])

    success = await db_move_tariff_up(session, tariff_id)

    if not success:
        await callback.answer("❌ Ошибка при перемещении тарифа", show_alert=True)
        return

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=text, reply_markup=markup)
    await callback.answer("✅ Тариф перемещен выше (-1)")


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("move_down|")), IsAdminFilter())
async def move_tariff_down(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    tariff_id = int(callback_data.action.split("|")[1])

    success = await db_move_tariff_down(session, tariff_id)

    if not success:
        await callback.answer("❌ Ошибка при перемещении тарифа", show_alert=True)
        return

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=text, reply_markup=markup)
    await callback.answer("✅ Тариф перемещен ниже (+1)")


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("quick_move_up|")), IsAdminFilter())
async def quick_move_tariff_up(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    parts = callback_data.action.split("|")
    tariff_id = int(parts[1])
    group_code = parts[2]

    success = await db_move_tariff_up(session, tariff_id)

    if not success:
        await callback.answer("❌ Ошибка при перемещении тарифа", show_alert=True)
        return

    await callback.answer("✅ Тариф перемещен выше (-1)")
    new_callback_data = AdminTariffCallback(action=f"arrange_group|{group_code}")
    await show_tariffs_arrangement(callback, new_callback_data, session)


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("quick_move_down|")), IsAdminFilter())
async def quick_move_tariff_down(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    parts = callback_data.action.split("|")
    tariff_id = int(parts[1])
    group_code = parts[2]

    success = await db_move_tariff_down(session, tariff_id)

    if not success:
        await callback.answer("❌ Ошибка при перемещении тарифа", show_alert=True)
        return

    await callback.answer("✅ Тариф перемещен ниже (+1)")
    new_callback_data = AdminTariffCallback(action=f"arrange_group|{group_code}")
    await show_tariffs_arrangement(callback, new_callback_data, session)
