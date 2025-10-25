import re

from collections import defaultdict
from datetime import datetime

import pytz

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, distinct, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import create_tariff
from database.models import Gift, Key, Server, Tariff
from database.tariffs import (
    create_subgroup_hash,
    find_subgroup_by_hash,
    get_tariffs,
    move_tariff_down as db_move_tariff_down,
    move_tariff_up as db_move_tariff_up,
)
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback
from .keyboard import (
    AdminTariffCallback,
    build_cancel_kb,
    build_edit_tariff_fields_kb,
    build_single_tariff_kb,
    build_tariff_arrangement_groups_kb,
    build_tariff_groups_kb,
    build_tariff_list_kb,
    build_tariff_menu_kb,
    build_tariffs_arrangement_kb,
)


router = Router()


class TariffCreateState(StatesGroup):
    group = State()
    name = State()
    duration = State()
    price = State()
    traffic = State()
    confirm_more = State()
    device_limit = State()
    vless = State()


class TariffEditState(StatesGroup):
    choosing_field = State()
    editing_value = State()


class TariffSubgroupState(StatesGroup):
    selecting_tariffs = State()
    entering_subgroup_title = State()


class SubgroupEditState(StatesGroup):
    entering_new_title = State()
    confirming_deletion = State()
    editing_tariffs = State()


MAX_TARIFF_NAME_LENGTH = 40
MAX_SUBGROUP_TITLE_LENGTH = 40


def validate_tariff_name(name: str) -> tuple[bool, str]:
    if len(name) > MAX_TARIFF_NAME_LENGTH:
        return False, f"Название тарифа слишком длинное. Максимум {MAX_TARIFF_NAME_LENGTH} символов."
    return True, ""


def validate_subgroup_title(title: str) -> tuple[bool, str]:
    if len(title) > MAX_SUBGROUP_TITLE_LENGTH:
        return False, f"Название подгруппы слишком длинное. Максимум {MAX_SUBGROUP_TITLE_LENGTH} символов."
    return True, ""


@router.callback_query(AdminPanelCallback.filter(F.action == "tariffs"), IsAdminFilter())
async def handle_tariff_menu(callback_query: CallbackQuery):
    text = (
        "<b>💸 Управление тарифами</b>\n\n"
        "Вы можете выполнить следующие действия:\n\n"
        "<b>🆕 Создать тариф</b>\n"
        "<blockquote>• Установите длительность (в днях)\n"
        "• Задайте цену (в рублях)\n"
        "• Задайте лимит устройств (hwid/ip_limit)\n"
        "• Укажите лимит трафика (в ГБ)</blockquote>\n\n"
        "<b>📋 Редактировать тарифы</b>\n"
        "<blockquote>• Просматривайте список текущих тарифов\n"
        "• Изменяйте параметры или удаляйте при необходимости</blockquote>"
    )
    await callback_query.message.edit_text(text=text, reply_markup=build_tariff_menu_kb())


@router.callback_query(AdminTariffCallback.filter(F.action == "create"), IsAdminFilter())
async def start_tariff_creation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TariffCreateState.group)
    await callback.message.edit_text(
        "📁 Введите <b>код группы</b>, в которую вы хотите добавить тариф.\n\n"
        "Например: <code>basic</code>, <code>vip</code>, <code>business</code>\n\n"
        "<b>Специальные группы:</b>\n"
        "• <code>discounts</code> — тарифы со скидкой\n"
        "• <code>discounts_max</code> — тарифы с максимальной скидкой\n"
        "• <code>gifts</code> — тарифы для подарков\n"
        "• <code>trial</code> — тариф для пробного периода",
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.group, IsAdminFilter())
async def process_tariff_group(message: Message, state: FSMContext):
    group_code = message.text.strip().lower()

    if not re.fullmatch(r"[a-z0-9_-]+", group_code):
        await message.answer(
            "❌ Код группы должен содержать только латинские буквы, цифры, дефисы и подчёркивания.\n\nПовторите ввод:",
            reply_markup=build_cancel_kb(),
        )
        return

    await state.update_data(group_code=group_code)
    await state.set_state(TariffCreateState.name)
    await message.answer(
        "📝 Введите <b>название тарифа</b>\n\n"
        "Например: <i>30 дней</i> или <i>1 месяц</i>\n\n"
        "<i>Это название будет отображаться пользователю при выборе тарифа</i>",
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.name, IsAdminFilter())
async def process_tariff_name(message: Message, state: FSMContext):
    name = message.text.strip()

    is_valid, error_msg = validate_tariff_name(name)
    if not is_valid:
        await message.answer(
            f"❌ {error_msg}\n\nПовторите ввод:",
            reply_markup=build_cancel_kb(),
        )
        return

    await state.update_data(name=name)
    await state.set_state(TariffCreateState.duration)
    await message.answer(
        "📅 Введите <b>длительность тарифа в днях</b> (например: <i>30</i>):",
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.duration, IsAdminFilter())
async def process_tariff_duration(message: Message, state: FSMContext):
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное количество дней (целое число больше 0):")
        return

    await state.update_data(duration_days=days)
    await state.set_state(TariffCreateState.price)
    await message.answer(
        "💰 Введите <b>цену тарифа в рублях</b> (например: <i>150</i>)\n\n"
        "<i>Будет показано клиенту при выборе тарифа</i>",
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.price, IsAdminFilter())
async def process_tariff_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную цену (целое число 0 или больше):")
        return

    await state.update_data(price_rub=price)
    await state.set_state(TariffCreateState.traffic)
    await message.answer(
        "📦 Введите <b>лимит трафика в ГБ</b> (например: <i>100</i>, 0 — безлимит):",
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.traffic, IsAdminFilter())
async def process_tariff_traffic(message: Message, state: FSMContext):
    try:
        traffic = int(message.text.strip())
        if traffic < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректный лимит трафика (целое число 0 или больше):")
        return

    await state.update_data(traffic_limit=traffic if traffic > 0 else None)
    await state.set_state(TariffCreateState.device_limit)
    await message.answer(
        "📱 Введите <b>лимит устройств (HWID)</b> для тарифа (например: <i>3</i>, 0 — безлимит):",
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.device_limit, IsAdminFilter())
async def process_tariff_device_limit(message: Message, state: FSMContext):
    try:
        device_limit = int(message.text.strip())
        if device_limit < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректный лимит устройств (целое число 0 или больше):")
        return

    await state.update_data(device_limit=device_limit if device_limit > 0 else None)
    await state.set_state(TariffCreateState.vless)

    await message.answer(
        "🔗 Этот тариф для VLESS?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да (VLESS)", callback_data="create_vless|1"),
                    InlineKeyboardButton(text="❌ Нет", callback_data="create_vless|0"),
                ],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_tariff_creation")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("create_vless|"), TariffCreateState.vless, IsAdminFilter())
async def select_vless_creation(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, flag = callback.data.split("|", 1)
    vless_flag = flag == "1"

    data = await state.get_data()

    new_tariff = await create_tariff(
        session,
        {
            "name": data["name"],
            "group_code": data["group_code"],
            "duration_days": data["duration_days"],
            "price_rub": data["price_rub"],
            "traffic_limit": data["traffic_limit"],
            "device_limit": data.get("device_limit"),
            "vless": vless_flag,
        },
    )

    await state.set_state(TariffCreateState.confirm_more)
    await callback.message.edit_text(
        f"✅ Тариф <b>{new_tariff.name}</b> добавлен в группу <code>{data['group_code']}</code>.\n\n"
        "➕ Хотите добавить ещё один тариф в эту группу?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да", callback_data="add_more_tariff"),
                    InlineKeyboardButton(text="❌ Нет", callback_data="done_tariff_group"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "add_more_tariff", IsAdminFilter())
async def handle_add_more_tariff(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TariffCreateState.name)
    await callback.message.edit_text("📝 Введите <b>название следующего тарифа</b>:", reply_markup=build_cancel_kb())


@router.callback_query(F.data == "done_tariff_group", IsAdminFilter())
async def handle_done_tariff_group(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("✅ Группа тарифов успешно завершена.", reply_markup=build_tariff_menu_kb())


@router.callback_query(F.data == "cancel_tariff_creation", IsAdminFilter())
async def cancel_tariff_creation(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Создание тарифа отменено.", reply_markup=build_tariff_menu_kb())


@router.callback_query(AdminTariffCallback.filter(F.action == "list"), IsAdminFilter())
async def show_tariff_groups(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(
        select(distinct(Tariff.group_code)).where(Tariff.group_code.isnot(None)).order_by(Tariff.group_code)
    )
    groups = [row[0] for row in result.fetchall()]

    if not groups:
        await callback.message.edit_text("❌ Нет сохранённых тарифов.", reply_markup=build_tariff_menu_kb())
        return

    special_groups = {
        "discounts": "🔻 Скидки",
        "discounts_max": "🔻 Макс. скидки",
        "gifts": "🎁 Подарки",
        "trial": "🚀 Пробный период",
    }

    text = "<b>📋 Выберите тарифную группу:</b>\n\n"
    text += "<b>Специальные группы:</b>\n"
    for code, label in special_groups.items():
        status = "✅ создана" if code in groups else "❌ не создана"
        text += f"{label} — <code>{code}</code> — <b>{status}</b>\n"

    text += "\n"

    await callback.message.edit_text(text, reply_markup=build_tariff_groups_kb(groups))


@router.callback_query(AdminTariffCallback.filter(F.action == "arrange"), IsAdminFilter())
async def show_tariff_arrangement_menu(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(
        select(distinct(Tariff.group_code)).where(Tariff.group_code.isnot(None)).order_by(Tariff.group_code)
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


def tariff_to_dict(tariff) -> dict:
    if isinstance(tariff, dict):
        return tariff
    return {
        "id": tariff.id,
        "name": tariff.name,
        "price_rub": tariff.price_rub,
        "group_code": tariff.group_code,
        "subgroup_title": tariff.subgroup_title,
        "sort_order": tariff.sort_order,
    }


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("group|")), IsAdminFilter())
async def show_tariffs_in_group(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    group_code = callback_data.action.split("|")[1]

    tariffs = await get_tariffs(session, group_code=group_code)

    if not tariffs:
        await callback.message.edit_text("❌ В этой группе пока нет тарифов.")
        return

    tariff_dicts = [tariff_to_dict(t) for t in tariffs]

    await callback.message.edit_text(
        f"<b>📦 Тарифы группы: {group_code}</b>",
        reply_markup=build_tariff_list_kb(tariff_dicts),
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

    sorted_subgroups = sorted([k for k in grouped_tariffs if k], key=lambda x: (subgroup_weights.get(x, 999999), x))

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


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("view|")), IsAdminFilter())
async def view_tariff(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    tariff_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        return

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=text, reply_markup=markup)


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("delete|")), IsAdminFilter())
async def confirm_tariff_deletion(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    tariff_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        return

    group_code = tariff.group_code

    if group_code == "gifts":
        gift_check = await session.execute(select(Gift).where(Gift.tariff_id == tariff_id).limit(1))
        if gift_check.scalar_one_or_none():
            result = await session.execute(select(Tariff).where(Tariff.group_code == "gifts", Tariff.id != tariff_id))
            other_tariffs = result.scalars().all()

            if not other_tariffs:
                await callback.message.edit_text(
                    "❌ Нельзя удалить тариф — он используется в подарках, а других тарифов в группе 'gifts' нет.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="⬅️ Назад", callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack()
                                )
                            ]
                        ]
                    ),
                )
                return

            builder = InlineKeyboardBuilder()
            for t in other_tariffs:
                builder.button(
                    text=f"{t.name} — {t.price_rub}₽",
                    callback_data=f"confirm_delete_tariff_with_replace|{tariff_id}|{t.id}",
                )
            builder.button(text="❌ Отмена", callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack())

            await callback.message.edit_text(
                "<b>Этот тариф используется в подарках.</b>\n\n"
                "Выберите тариф, на который заменить его во всех подарках перед удалением:",
                reply_markup=builder.as_markup(),
            )
            return

    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите <b>удалить</b> этот тариф?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_delete_tariff|{tariff_id}"),
                    InlineKeyboardButton(
                        text="❌ Отмена", callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack()
                    ),
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith("confirm_delete_tariff_with_replace|"), IsAdminFilter())
async def delete_tariff_with_gift_replacement(callback: CallbackQuery, session: AsyncSession):
    _, tariff_id_str, replacement_id_str = callback.data.split("|")
    tariff_id = int(tariff_id_str)
    replacement_id = int(replacement_id_str)

    await session.execute(update(Gift).where(Gift.tariff_id == tariff_id).values(tariff_id=replacement_id))

    await session.execute(update(Key).where(Key.tariff_id == tariff_id).values(tariff_id=None))

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        return

    group_code = tariff.group_code

    await session.execute(delete(Tariff).where(Tariff.id == tariff_id))

    result = await session.execute(select(Tariff).where(Tariff.group_code == group_code))
    remaining_tariffs = result.scalars().all()
    if not remaining_tariffs:
        await session.execute(update(Server).where(Server.tariff_group == group_code).values(tariff_group=None))

    await session.commit()
    await callback.message.edit_text("🗑 Тариф удалён. Все подарки обновлены.", reply_markup=build_tariff_menu_kb())


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("edit|")), IsAdminFilter())
async def start_edit_tariff(callback: CallbackQuery, callback_data: AdminTariffCallback, state: FSMContext):
    tariff_id = int(callback_data.action.split("|")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(TariffEditState.choosing_field)
    await callback.message.edit_text(
        "<b>✏️ Что вы хотите изменить?</b>",
        reply_markup=build_edit_tariff_fields_kb(tariff_id),
    )


@router.callback_query(F.data.startswith("confirm_delete_tariff|"), IsAdminFilter())
async def delete_tariff(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split("|", 1)[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        return

    group_code = tariff.group_code

    await session.execute(update(Key).where(Key.tariff_id == tariff_id).values(tariff_id=None))
    await session.execute(delete(Tariff).where(Tariff.id == tariff_id))

    result = await session.execute(select(Tariff).where(Tariff.group_code == group_code))
    remaining_tariffs = result.scalars().all()

    if not remaining_tariffs:
        await session.execute(update(Server).where(Server.tariff_group == group_code).values(tariff_group=None))

    await session.commit()
    await callback.message.edit_text("🗑 Тариф успешно удалён.", reply_markup=build_tariff_menu_kb())


@router.callback_query(F.data.startswith("edit_field|"), IsAdminFilter())
async def ask_new_value(callback: CallbackQuery, state: FSMContext):
    _, _tariff_id, field = callback.data.split("|")
    await state.update_data(field=field)
    await state.set_state(TariffEditState.editing_value)

    if field == "vless":
        data = await state.get_data()
        tariff_id = int(data["tariff_id"])
        await callback.message.edit_text(
            "🔗 Установить флаг VLESS:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Да (VLESS)", callback_data=f"set_vless|{tariff_id}|1"),
                        InlineKeyboardButton(text="❌ Нет", callback_data=f"set_vless|{tariff_id}|0"),
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад",
                            callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack(),
                        )
                    ],
                ]
            ),
        )
        return

    field_names = {
        "name": "название тарифа",
        "duration_days": "длительность в днях",
        "price_rub": "цену в рублях",
        "traffic_limit": "лимит трафика в ГБ (0 — безлимит)",
        "device_limit": "лимит устройств (0 — безлимит)",
        "vless": "VLESS (да/нет)",
    }

    await callback.message.edit_text(
        f"✏️ Введите новое значение для <b>{field_names.get(field, field)}</b>:",
        reply_markup=build_cancel_kb(),
    )


@router.callback_query(F.data.startswith("set_vless|"), TariffEditState.editing_value, IsAdminFilter())
async def set_vless_flag(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, tariff_id_str, flag = callback.data.split("|", 2)
    tariff_id = int(tariff_id_str)
    vless_flag = flag == "1"

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        await state.clear()
        return

    tariff.vless = vless_flag
    tariff.updated_at = datetime.utcnow()
    await session.commit()
    await state.clear()

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=text, reply_markup=markup)


@router.message(TariffEditState.editing_value, IsAdminFilter())
async def apply_edit(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff_id = data["tariff_id"]
    field = data["field"]
    value = message.text.strip()

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await message.answer("❌ Тариф не найден.")
        await state.clear()
        return

    if field == "name":
        is_valid, error_msg = validate_tariff_name(value)
        if not is_valid:
            await message.answer(
                f"❌ {error_msg}\n\nПовторите ввод:",
                reply_markup=build_cancel_kb(),
            )
            return

    if field in ["duration_days", "price_rub", "traffic_limit", "device_limit"]:
        try:
            num = int(value)
            if num < 0:
                raise ValueError
            if field in ["traffic_limit", "device_limit"]:
                value = num if num > 0 else None
            else:
                value = num
        except ValueError:
            await message.answer("❌ Введите корректное число.")
            return

    setattr(tariff, field, value)
    tariff.updated_at = datetime.utcnow()

    await session.commit()
    await state.clear()

    text, markup = render_tariff_card(tariff)
    await message.answer(text=text, reply_markup=markup)


@router.callback_query(F.data.startswith("toggle_active|"), IsAdminFilter())
async def toggle_tariff_status(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        return

    tariff.is_active = not tariff.is_active
    await session.commit()

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=text, reply_markup=markup)


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("create|")), IsAdminFilter())
async def start_tariff_creation_existing_group(
    callback: CallbackQuery, callback_data: AdminTariffCallback, state: FSMContext
):
    group_code = callback_data.action.split("|", 1)[1]
    await state.update_data(group_code=group_code)
    await state.set_state(TariffCreateState.name)
    await callback.message.edit_text(
        f"📦 Добавление нового тарифа в группу <code>{group_code}</code>\n\n📝 Введите <b>название тарифа</b>:",
        reply_markup=build_cancel_kb(),
    )


def render_tariff_card(tariff: Tariff) -> tuple[str, InlineKeyboardMarkup]:
    traffic_text = f"{tariff.traffic_limit} ГБ" if tariff.traffic_limit else "Безлимит"
    device_text = f"{tariff.device_limit}" if tariff.device_limit is not None else "Безлимит"
    sort_order = getattr(tariff, "sort_order", 1)
    vless_text = "Да" if getattr(tariff, "vless", False) else "Нет"

    text = (
        f"<b>📄 Тариф: {tariff.name}</b>\n\n"
        f"📁 Группа: <code>{tariff.group_code}</code>\n"
        f"📅 Длительность: <b>{tariff.duration_days} дней</b>\n"
        f"💰 Стоимость: <b>{tariff.price_rub}₽</b>\n"
        f"📦 Трафик: <b>{traffic_text}</b>\n"
        f"📱 Устройств: <b>{device_text}</b>\n"
        f"🔗 VLESS: <b>{vless_text}</b>\n"
        f"🔢 Позиция: <b>{sort_order}</b>\n"
        f"{'✅ Активен' if tariff.is_active else '⛔ Отключен'}"
    )

    return text, build_single_tariff_kb(tariff.id, tariff.group_code)


@router.callback_query(F.data.startswith("start_subgrouping|"), IsAdminFilter())
async def start_subgrouping(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    group_code = callback.data.split("|", 1)[1]

    tariffs = await get_tariffs(session, group_code=group_code)
    tariffs = [t for t in tariffs if not t.get("subgroup_title") or t.get("subgroup_title") == ""]

    if not tariffs:
        await callback.message.edit_text(
            "❌ Нет доступных тарифов для группировки.\n\nВсе тарифы уже находятся в подгруппах.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад", callback_data=AdminTariffCallback(action=f"group|{group_code}").pack()
                        )
                    ]
                ]
            ),
        )
        return

    await state.set_state(TariffSubgroupState.selecting_tariffs)
    await state.update_data(group_code=group_code, selected_tariff_ids=[])

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.row(InlineKeyboardButton(text=f"{tariff.get('name')}", callback_data=f"sub_select|{tariff.get('id')}"))

    builder.row(
        InlineKeyboardButton(text="➡️ Продолжить", callback_data="subgroup_continue"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_subgrouping"),
    )

    await callback.message.edit_text(
        "Выберите тарифы, которые нужно объединить в подгруппу:", reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("sub_select|"), TariffSubgroupState.selecting_tariffs, IsAdminFilter())
async def toggle_tariff_subgroup_selection(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])
    data = await state.get_data()
    selected = set(data.get("selected_tariff_ids", []))

    if tariff_id in selected:
        selected.remove(tariff_id)
    else:
        selected.add(tariff_id)

    await state.update_data(selected_tariff_ids=list(selected))

    group_code = data["group_code"]
    tariffs = await get_tariffs(session, group_code=group_code)
    tariffs = [t for t in tariffs if not t.get("subgroup_title") or t.get("subgroup_title") == ""]

    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        is_selected = tariff.get("id") in selected
        prefix = "✅ " if is_selected else ""
        builder.row(
            InlineKeyboardButton(text=f"{prefix}{tariff.get('name')}", callback_data=f"sub_select|{tariff.get('id')}")
        )

    builder.row(
        InlineKeyboardButton(text="➡️ Продолжить", callback_data="subgroup_continue"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_subgrouping"),
    )

    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())


@router.callback_query(
    F.data == "subgroup_continue",
    TariffSubgroupState.selecting_tariffs,
    IsAdminFilter(),
)
async def ask_subgroup_title(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("selected_tariff_ids"):
        await callback.answer("Выберите хотя бы один тариф", show_alert=True)
        return

    await state.set_state(TariffSubgroupState.entering_subgroup_title)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_subgrouping")]]
    )

    await callback.message.edit_text(
        "📁 Введите название новой подгруппы:",
        reply_markup=keyboard,
    )


@router.message(TariffSubgroupState.entering_subgroup_title, IsAdminFilter())
async def apply_subgroup_title(message: Message, state: FSMContext, session: AsyncSession):
    title = message.text.strip()

    is_valid, error_msg = validate_subgroup_title(title)
    if not is_valid:
        await message.answer(
            f"❌ {error_msg}\n\nПовторите ввод:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_subgrouping")]]
            ),
        )
        return

    data = await state.get_data()
    selected_ids = data.get("selected_tariff_ids", [])

    if not selected_ids:
        await message.answer("❌ Нет выбранных тарифов.")
        await state.clear()
        return

    await session.execute(
        update(Tariff).where(Tariff.id.in_(selected_ids)).values(subgroup_title=title, updated_at=datetime.utcnow())
    )
    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ {len(selected_ids)} тарифов сгруппированы в подгруппу: <b>{title}</b>.",
        reply_markup=build_tariff_menu_kb(),
    )


@router.callback_query(F.data == "cancel_subgrouping", IsAdminFilter())
async def cancel_subgrouping(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Группировка в подгруппу отменена.", reply_markup=build_tariff_menu_kb())


@router.callback_query(F.data.startswith("view_subgroup|"), IsAdminFilter())
async def view_subgroup_tariffs(callback: CallbackQuery, session: AsyncSession):
    _, subgroup_hash, group_code = callback.data.split("|", 2)

    subgroup_title = await find_subgroup_by_hash(session, subgroup_hash, group_code)

    if not subgroup_title:
        await callback.message.edit_text("❌ Подгруппа не найдена.")
        return

    tariffs = await get_tariffs(session, group_code=group_code)
    tariffs = [t for t in tariffs if t.get("subgroup_title") == subgroup_title]

    if not tariffs:
        await callback.message.edit_text("❌ В этой подгруппе пока нет тарифов.")
        return

    tariffs_dicts = [tariff_to_dict(t) for t in tariffs]

    builder = InlineKeyboardBuilder()
    for t in tariffs_dicts:
        title = f"{t['name']} — {t['price_rub']}₽"
        builder.row(
            InlineKeyboardButton(
                text=title,
                callback_data=AdminTariffCallback(action=f"view|{t['id']}").pack(),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="📝 Переименовать подгруппу",
            callback_data=f"rename_subgroup|{subgroup_hash}|{group_code}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="✏️ Редактировать подгруппу",
            callback_data=f"edit_subgroup_tariffs|{subgroup_hash}|{group_code}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🗑 Удалить подгруппу",
            callback_data=f"delete_subgroup|{subgroup_hash}|{group_code}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=AdminTariffCallback(action=f"group|{group_code}").pack(),
        )
    )

    await callback.message.edit_text(
        f"<b>📂 Подгруппа: {subgroup_title}</b>",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("rename_subgroup|"), IsAdminFilter())
async def start_rename_subgroup(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, subgroup_hash, group_code = callback.data.split("|", 2)

    subgroup_title = await find_subgroup_by_hash(session, subgroup_hash, group_code)

    if not subgroup_title:
        await callback.message.edit_text("❌ Подгруппа не найдена.")
        return

    await state.update_data(
        subgroup_title=subgroup_title,
        group_code=group_code,
        subgroup_hash=subgroup_hash,
    )

    await state.set_state(SubgroupEditState.entering_new_title)
    await callback.message.edit_text(
        f"📝 Введите новое название подгруппы:\n<b>{subgroup_title}</b>\n\nИли нажмите Отмена.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}")]
            ]
        ),
    )


@router.message(SubgroupEditState.entering_new_title, IsAdminFilter())
async def save_new_subgroup_title(message: Message, state: FSMContext, session: AsyncSession):
    new_title = message.text.strip()

    is_valid, error_msg = validate_subgroup_title(new_title)
    if not is_valid:
        data = await state.get_data()
        subgroup_hash = data.get("subgroup_hash")
        group_code = data.get("group_code")

        await message.answer(
            f"❌ {error_msg}\n\nПовторите ввод:",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ Отмена", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}"
                        )
                    ]
                ]
            ),
        )
        return

    data = await state.get_data()
    old_title = data["subgroup_title"]
    group_code = data["group_code"]

    await session.execute(
        update(Tariff)
        .where(
            Tariff.group_code == group_code,
            Tariff.subgroup_title == old_title,
        )
        .values(subgroup_title=new_title)
    )
    await session.commit()
    await state.clear()

    create_subgroup_hash(new_title, group_code)

    await message.answer(
        f"✅ Подгруппа <b>{old_title}</b> переименована в <b>{new_title}</b>.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад", callback_data=AdminTariffCallback(action=f"group|{group_code}").pack()
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith("delete_subgroup|"), IsAdminFilter())
async def confirm_delete_subgroup(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, subgroup_hash, group_code = callback.data.split("|", 2)

    subgroup_title = await find_subgroup_by_hash(session, subgroup_hash, group_code)

    if not subgroup_title:
        await callback.message.edit_text("❌ Подгруппа не найдена.")
        return

    await state.update_data(
        subgroup_title=subgroup_title,
        group_code=group_code,
        subgroup_hash=subgroup_hash,
    )
    await state.set_state(SubgroupEditState.confirming_deletion)

    await callback.message.edit_text(
        f"❗ Вы уверены, что хотите <b>удалить</b> подгруппу <b>{subgroup_title}</b>?\n"
        "Это удалит поле `subgroup_title` у всех связанных тарифов.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Удалить", callback_data="confirm_subgroup_deletion"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}"),
                ]
            ]
        ),
    )


@router.callback_query(F.data == "confirm_subgroup_deletion", SubgroupEditState.confirming_deletion, IsAdminFilter())
async def perform_subgroup_deletion(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    subgroup_title = data["subgroup_title"]
    group_code = data["group_code"]

    await session.execute(
        update(Tariff)
        .where(Tariff.group_code == group_code, Tariff.subgroup_title == subgroup_title)
        .values(subgroup_title=None)
    )
    await session.commit()
    await state.clear()

    await callback.message.edit_text(
        f"✅ Подгруппа <b>{subgroup_title}</b> удалена.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад", callback_data=AdminTariffCallback(action=f"group|{group_code}").pack()
                    )
                ]
            ]
        ),
    )


@router.callback_query(F.data.startswith("edit_subgroup_tariffs|"), IsAdminFilter())
async def start_edit_subgroup_tariffs(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, subgroup_hash, group_code = callback.data.split("|", 2)

    subgroup_title = await find_subgroup_by_hash(session, subgroup_hash, group_code)

    if not subgroup_title:
        await callback.message.edit_text("❌ Подгруппа не найдена.")
        return

    all_tariffs_to_show = await get_tariffs(session, group_code=group_code)
    all_tariffs_to_show = [
        t
        for t in all_tariffs_to_show
        if t.get("subgroup_title") == subgroup_title or not t.get("subgroup_title") or t.get("subgroup_title") == ""
    ]

    subgroup_tariff_ids = {t.get("id") for t in all_tariffs_to_show if t.get("subgroup_title") == subgroup_title}

    if not all_tariffs_to_show:
        await callback.message.edit_text(
            "❌ Нет доступных тарифов для редактирования.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}")]
                ]
            ),
        )
        return

    await state.set_state(SubgroupEditState.editing_tariffs)
    await state.update_data(
        subgroup_title=subgroup_title,
        group_code=group_code,
        subgroup_hash=subgroup_hash,
        selected_tariff_ids=list(subgroup_tariff_ids),
    )

    builder = InlineKeyboardBuilder()
    for tariff in all_tariffs_to_show:
        is_in_subgroup = tariff.get("id") in subgroup_tariff_ids
        prefix = "✅ " if is_in_subgroup else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix}{tariff.get('name')}", callback_data=f"edit_sub_toggle|{tariff.get('id')}"
            )
        )

    builder.row(
        InlineKeyboardButton(text="💾 Сохранить", callback_data="edit_sub_save"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}"),
    )

    await callback.message.edit_text(
        f"✏️ <b>Редактирование подгруппы: {subgroup_title}</b>\n\n"
        "✅ - тарифы в подгруппе\n\n"
        "Нажмите на тариф, чтобы добавить/убрать его:",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("edit_sub_toggle|"), SubgroupEditState.editing_tariffs, IsAdminFilter())
async def toggle_tariff_in_subgroup_edit(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])
    data = await state.get_data()
    selected_ids = set(data.get("selected_tariff_ids", []))

    if tariff_id in selected_ids:
        selected_ids.remove(tariff_id)
    else:
        selected_ids.add(tariff_id)

    await state.update_data(selected_tariff_ids=list(selected_ids))

    subgroup_title = data["subgroup_title"]
    group_code = data["group_code"]
    subgroup_hash = data["subgroup_hash"]

    all_tariffs_to_show = await get_tariffs(session, group_code=group_code)
    all_tariffs_to_show = [
        t
        for t in all_tariffs_to_show
        if t.get("subgroup_title") == subgroup_title or not t.get("subgroup_title") or t.get("subgroup_title") == ""
    ]

    builder = InlineKeyboardBuilder()
    for tariff in all_tariffs_to_show:
        is_selected = tariff.get("id") in selected_ids
        prefix = "✅ " if is_selected else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{prefix}{tariff.get('name')}", callback_data=f"edit_sub_toggle|{tariff.get('id')}"
            )
        )

    builder.row(
        InlineKeyboardButton(text="💾 Сохранить", callback_data="edit_sub_save"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}"),
    )

    await callback.message.edit_reply_markup(reply_markup=builder.as_markup())


@router.callback_query(F.data == "edit_sub_save", SubgroupEditState.editing_tariffs, IsAdminFilter())
async def save_subgroup_tariffs_changes(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    subgroup_title = data["subgroup_title"]
    group_code = data["group_code"]
    subgroup_hash = data["subgroup_hash"]
    selected_tariff_ids = set(data.get("selected_tariff_ids", []))

    result = await session.execute(
        select(Tariff).where(Tariff.group_code == group_code, Tariff.subgroup_title == subgroup_title)
    )
    current_subgroup_tariffs = result.scalars().all()
    current_tariff_ids = {t.id for t in current_subgroup_tariffs}

    to_add = selected_tariff_ids - current_tariff_ids
    to_remove = current_tariff_ids - selected_tariff_ids

    if to_remove:
        await session.execute(
            update(Tariff).where(Tariff.id.in_(to_remove)).values(subgroup_title=None, updated_at=datetime.utcnow())
        )

    if to_add:
        await session.execute(
            update(Tariff)
            .where(Tariff.id.in_(to_add))
            .values(subgroup_title=subgroup_title, updated_at=datetime.utcnow())
        )

    await session.commit()
    await state.clear()

    if not selected_tariff_ids:
        await callback.message.edit_text(
            f"✅ Подгруппа <b>{subgroup_title}</b> была расформирована.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад к группе тарифов",
                            callback_data=AdminTariffCallback(action=f"group|{group_code}").pack(),
                        )
                    ]
                ]
            ),
        )
        return

    changes_text = []
    if to_add:
        added_names = []
        for tariff_id in to_add:
            result = await session.execute(select(Tariff.name).where(Tariff.id == tariff_id))
            name = result.scalar_one()
            if name:
                added_names.append(name)
        changes_text.append(f"➕ Добавлено: {', '.join(added_names)}")

    if to_remove:
        removed_names = []
        for tariff_id in to_remove:
            result = await session.execute(select(Tariff.name).where(Tariff.id == tariff_id))
            name = result.scalar_one()
            if name:
                removed_names.append(name)
        changes_text.append(f"➖ Удалено: {', '.join(removed_names)}")

    if not changes_text:
        changes_text.append("Изменений не было")

    await callback.message.edit_text(
        f"✅ <b>Подгруппа обновлена: {subgroup_title}</b>\n\n{chr(10).join(changes_text)}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад к подгруппе", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}"
                    )
                ]
            ]
        ),
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
