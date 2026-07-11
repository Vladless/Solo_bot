from collections import defaultdict
from datetime import datetime

import pytz

from aiogram import F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Tariff
from database.tariffs import (
    find_subgroup_by_hash,
    get_tariffs,
    move_subgroup as db_move_subgroup,
    move_tariff_down as db_move_tariff_down,
    move_tariff_up as db_move_tariff_up,
)
from filters.admin import IsAdminFilter
from handlers.keys.utils import order_tariff_items

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
    tariffs_data["subgroup_weights"]

    if not tariffs:
        await callback.message.edit_text("❌ В этой группе пока нет активных тарифов.")
        return

    grouped_tariffs = defaultdict(list)
    for t in tariffs:
        grouped_tariffs[t.get("subgroup_title")].append(t)

    moscow_tz = pytz.timezone("Europe/Moscow")
    now = datetime.now(moscow_tz)
    current_time = now.strftime("%d.%m.%y %H:%M:%S МСК")

    text = (
        f"🔢 <b>Итоговая сортировка тарифов в группе: {group_code}</b>\n\n"
        "Порядок как видит пользователь (сквозной по позиции):\n\n"
    )
    for kind, payload in order_tariff_items(grouped_tariffs):
        if kind == "tariff":
            text += f"• {payload.get('name')} <code>[поз: {payload.get('sort_order') or 1}]</code>\n"
        else:
            sub_tariffs = grouped_tariffs[payload]
            min_pos = min((t.get("sort_order") or 1) for t in sub_tariffs)
            text += f"📁 <b>{payload}</b> <code>[поз: {min_pos}]</code>\n"
            for t in sub_tariffs:
                text += f"  └ {t.get('name')} <code>[поз: {t.get('sort_order') or 1}]</code>\n"

    text += f"\n{current_time}"

    await callback.message.edit_text(
        text,
        reply_markup=build_tariffs_arrangement_kb(group_code, tariffs),
    )


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("move_up|")), IsAdminFilter(), flags={"popup": True})
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


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("move_down|")), IsAdminFilter(), flags={"popup": True})
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


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("quick_move_up|")), IsAdminFilter(), flags={"popup": True})
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


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("quick_move_down|")), IsAdminFilter(), flags={"popup": True})
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


async def _move_subgroup(callback: CallbackQuery, session: AsyncSession, direction: str) -> None:
    _, subgroup_hash, group_code = callback.data.split("|", 2)
    subgroup_title = await find_subgroup_by_hash(session, subgroup_hash, group_code)
    if not subgroup_title:
        await callback.answer("❌ Подгруппа не найдена", show_alert=True)
        return
    ok = await db_move_subgroup(session, group_code, subgroup_title, direction)
    if not ok:
        await callback.answer("⛔ Дальше двигать некуда")
        return
    await callback.answer("✅ Подгруппа перемещена выше" if direction == "up" else "✅ Подгруппа перемещена ниже")
    await show_tariffs_arrangement(callback, AdminTariffCallback(action=f"arrange_group|{group_code}"), session)


@router.callback_query(F.data.startswith("submove_up|"), IsAdminFilter())
async def quick_move_subgroup_up(callback: CallbackQuery, session: AsyncSession):
    await _move_subgroup(callback, session, "up")


@router.callback_query(F.data.startswith("submove_down|"), IsAdminFilter())
async def quick_move_subgroup_down(callback: CallbackQuery, session: AsyncSession):
    await _move_subgroup(callback, session, "down")
