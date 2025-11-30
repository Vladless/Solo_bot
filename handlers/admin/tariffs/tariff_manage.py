import re

from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import delete, distinct, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import create_tariff
from database.models import Gift, Key, Server, Tariff
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback
from . import router
from .keyboard import (
    AdminTariffCallback,
    build_cancel_kb,
    build_edit_tariff_fields_kb,
    build_tariff_groups_kb,
    build_tariff_list_kb,
    build_tariff_menu_kb,
)
from .tariff_states import TariffCreateState, TariffEditState
from .tariff_utils import render_tariff_card, validate_tariff_name


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
        "🔗 Этот тариф для выдачи VLESS (конфигурация для роутера)?",
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


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("group|")), IsAdminFilter())
async def show_tariffs_in_group(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    from database.tariffs import get_tariffs

    from .tariff_utils import tariff_to_dict

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

            builder = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=f"{t.name} — {t.price_rub}₽",
                            callback_data=f"confirm_delete_tariff_with_replace|{tariff_id}|{t.id}",
                        )
                    ]
                    for t in other_tariffs
                ]
                + [
                    [
                        InlineKeyboardButton(
                            text="❌ Отмена", callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack()
                        )
                    ]
                ]
            )

            await callback.message.edit_text(
                "<b>Этот тариф используется в подарках.</b>\n\n"
                "Выберите тариф, на который заменить его во всех подарках перед удалением:",
                reply_markup=builder,
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


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("edit|")), IsAdminFilter())
async def start_edit_tariff(callback: CallbackQuery, callback_data: AdminTariffCallback, state: FSMContext):
    tariff_id = int(callback_data.action.split("|")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(TariffEditState.choosing_field)
    await callback.message.edit_text(
        "<b>✏️ Что вы хотите изменить?</b>",
        reply_markup=build_edit_tariff_fields_kb(tariff_id),
    )


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
        "external_squad": "внешний сквад (0 — убрать)",
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

    if field == "external_squad":
        if value in ("", "0", "-"):
            value = None
        setattr(tariff, field, value)
        tariff.updated_at = datetime.utcnow()
        await session.commit()
        await state.clear()

        text, markup = render_tariff_card(tariff)
        await message.answer(text=text, reply_markup=markup)
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


@router.callback_query(F.data.startswith("toggle_configurable|"), IsAdminFilter())
async def toggle_tariff_configurable(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text("❌ Тариф не найден.")
        return

    current = bool(tariff.configurable)
    tariff.configurable = not current
    tariff.updated_at = datetime.utcnow()

    await session.commit()

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=text, reply_markup=markup)
