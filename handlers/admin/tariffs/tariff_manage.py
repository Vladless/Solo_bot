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
from settings.buttons import BACK

from ..panel.headers import menu_text, quote, section
from ..panel.keyboard import AdminPanelCallback
from . import router
from .keyboard import (
    AdminTariffCallback,
    build_cancel_kb,
    build_edit_tariff_fields_kb,
    build_tariff_groups_kb,
    build_tariff_list_kb,
    build_tariff_menu_kb,
    build_tariff_visibility_kb,
)
from .tariff_states import TariffCreateState, TariffEditState
from .tariff_utils import MAX_TARIFF_NAME_LENGTH, render_tariff_card, validate_tariff_name


@router.callback_query(AdminPanelCallback.filter(F.action == "tariffs"), IsAdminFilter())
async def handle_tariff_menu(callback_query: CallbackQuery):
    text = menu_text(
        "Тарифы",
        "Сроки, цены и лимиты подписок.",
        quote("Новый тариф — создать с нуля.\nМои тарифы — правка и удаление.\nРасположение — порядок в меню клиента."),
    )
    await callback_query.message.edit_text(text=text, reply_markup=build_tariff_menu_kb())


@router.callback_query(AdminTariffCallback.filter(F.action == "create"), IsAdminFilter())
async def start_tariff_creation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TariffCreateState.group)
    await callback.message.edit_text(
        menu_text(
            "Группа тарифа",
            "Введите код группы.",
            quote("Свободный код: <code>basic</code>, <code>vip</code>, <code>business</code>."),
            quote("Скидки для тёплых лидов:\n<code>discounts</code>, <code>discounts_max</code>"),
            quote("Для холодных:\n<code>cold_discounts</code>, <code>cold_discounts_max</code>"),
            quote("Служебные:\n<code>gifts</code> — подарки, <code>trial</code> — пробный период"),
            markup=build_cancel_kb(),
        ),
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.group, IsAdminFilter())
async def process_tariff_group(message: Message, state: FSMContext):
    group_code = message.text.strip().lower()

    if not re.fullmatch(r"[a-z0-9_-]+", group_code):
        await message.answer(
            menu_text(
                "Тарифы",
                "❌ Код группы должен содержать только латинские буквы, цифры, дефисы и подчёркивания.",
                quote("Повторите ввод:"),
                markup=build_cancel_kb(),
            ),
            reply_markup=build_cancel_kb(),
        )
        return

    await state.update_data(group_code=group_code)
    await state.set_state(TariffCreateState.name)
    await message.answer(
        menu_text(
            "Тарифы",
            "📝 Введите <b>название тарифа</b>",
            quote("Например: <i>30 дней</i> или <i>1 месяц</i>"),
            quote("<i>Его увидит клиент при выборе тарифа.</i>"),
            markup=build_cancel_kb(),
        ),
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.name, IsAdminFilter())
async def process_tariff_name(message: Message, state: FSMContext):
    name = message.text.strip()

    is_valid, error_msg = validate_tariff_name(name)
    if not is_valid:
        await message.answer(
            menu_text("Тарифы", f"❌ {error_msg}", quote("Повторите ввод:"), markup=build_cancel_kb()),
            reply_markup=build_cancel_kb(),
        )
        return

    await state.update_data(name=name)
    await state.set_state(TariffCreateState.duration)
    await message.answer(
        menu_text(
            "Тарифы", "📅 Введите <b>длительность тарифа в днях</b> (например: <i>30</i>):", markup=build_cancel_kb()
        ),
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.duration, IsAdminFilter())
async def process_tariff_duration(message: Message, state: FSMContext):
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer(menu_text("Тарифы", "❌ Нужно число больше нуля."))
        return

    await state.update_data(duration_days=days)
    await state.set_state(TariffCreateState.price)
    await message.answer(
        menu_text(
            "Тарифы",
            "💰 Введите <b>цену тарифа в рублях</b> (например: <i>150</i>)",
            quote("<i>Будет показано клиенту при выборе тарифа</i>"),
            markup=build_cancel_kb(),
        ),
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.price, IsAdminFilter())
async def process_tariff_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer(menu_text("Тарифы", "❌ Нужно число от нуля."))
        return

    await state.update_data(price_rub=price)
    await state.set_state(TariffCreateState.traffic)
    await message.answer(
        menu_text(
            "Тарифы",
            "📦 Введите <b>лимит трафика в ГБ</b> (например: <i>100</i>, 0 — безлимит):",
            markup=build_cancel_kb(),
        ),
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.traffic, IsAdminFilter())
async def process_tariff_traffic(message: Message, state: FSMContext):
    try:
        traffic = int(message.text.strip())
        if traffic < 0:
            raise ValueError
    except ValueError:
        await message.answer(menu_text("Тарифы", "❌ Нужно число от нуля."))
        return

    await state.update_data(traffic_limit=traffic if traffic > 0 else None)
    await state.set_state(TariffCreateState.device_limit)
    await message.answer(
        menu_text(
            "Тарифы",
            "📱 Введите <b>лимит устройств (HWID)</b> для тарифа (например: <i>3</i>, 0 — безлимит):",
            markup=build_cancel_kb(),
        ),
        reply_markup=build_cancel_kb(),
    )


@router.message(TariffCreateState.device_limit, IsAdminFilter())
async def process_tariff_device_limit(message: Message, state: FSMContext):
    try:
        device_limit = int(message.text.strip())
        if device_limit < 0:
            raise ValueError
    except ValueError:
        await message.answer(menu_text("Тарифы", "❌ Нужно число от нуля."))
        return

    await state.update_data(device_limit=device_limit if device_limit > 0 else None)
    await state.set_state(TariffCreateState.vless)

    await message.answer(
        menu_text("Тарифы", "🔗 Этот тариф для выдачи VLESS (конфигурация для роутера)?"),
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

    from .tariff_utils import check_tariff_price_monotonicity, format_price_monotonicity_warning

    warn_block = format_price_monotonicity_warning(await check_tariff_price_monotonicity(session, new_tariff))

    await state.set_state(TariffCreateState.confirm_more)
    await callback.message.edit_text(
        menu_text(
            "Тарифы",
            f"✅ Тариф <b>{new_tariff.name}</b> добавлен в группу <code>{data['group_code']}</code>.{warn_block}",
            quote("➕ Хотите добавить ещё один тариф в эту группу?"),
        ),
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
    await callback.message.edit_text(
        menu_text("Тарифы", "📝 Введите <b>название следующего тарифа</b>:", markup=build_cancel_kb()),
        reply_markup=build_cancel_kb(),
    )


@router.callback_query(F.data == "done_tariff_group", IsAdminFilter())
async def handle_done_tariff_group(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        menu_text("Тарифы", "✅ Группа тарифов завершена.", markup=build_tariff_menu_kb()),
        reply_markup=build_tariff_menu_kb(),
    )


@router.callback_query(F.data == "cancel_tariff_creation", IsAdminFilter())
async def cancel_tariff_creation(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        menu_text("Тарифы", "❌ Создание тарифа отменено.", markup=build_tariff_menu_kb()),
        reply_markup=build_tariff_menu_kb(),
    )


@router.callback_query(AdminTariffCallback.filter(F.action == "list"), IsAdminFilter())
async def show_tariff_groups(callback: CallbackQuery, session: AsyncSession):
    result = await session.execute(
        select(distinct(Tariff.group_code)).where(Tariff.group_code.isnot(None)).order_by(Tariff.group_code)
    )
    groups = [row[0] for row in result.fetchall()]

    if not groups:
        await callback.message.edit_text(
            menu_text("Тарифы", "❌ Нет сохранённых тарифов.", markup=build_tariff_menu_kb()),
            reply_markup=build_tariff_menu_kb(),
        )
        return

    special_groups = {
        "discounts": "Скидки",
        "discounts_max": "Скидки макс",
        "cold_discounts": "Холодные",
        "cold_discounts_max": "Холодные макс",
        "gifts": "Подарки",
        "trial": "Пробный",
    }

    special = [f"{label}: {'есть' if code in groups else 'нет'}" for code, label in special_groups.items()]
    text = menu_text("Группы тарифов", "Выберите группу.", section("⭐ Особые группы", *special))

    await callback.message.edit_text(text, reply_markup=build_tariff_groups_kb(groups))


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("group|")), IsAdminFilter())
async def show_tariffs_in_group(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    from database.tariffs import get_tariffs

    from .tariff_utils import tariff_to_dict

    group_code = callback_data.action.split("|")[1]

    tariffs = await get_tariffs(session, group_code=group_code)

    if not tariffs:
        await callback.message.edit_text(menu_text("Тарифы", "❌ В этой группе пока нет тарифов."))
        return

    tariff_dicts = [tariff_to_dict(t) for t in tariffs]

    await callback.message.edit_text(
        menu_text("Тарифы группы", f"Группа <b>{group_code}</b>", markup=build_tariff_list_kb(tariff_dicts)),
        reply_markup=build_tariff_list_kb(tariff_dicts),
    )


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("view|")), IsAdminFilter())
async def view_tariff(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    tariff_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        return

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("duplicate|")), IsAdminFilter())
async def duplicate_tariff(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    tariff_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    original = result.scalar_one_or_none()

    if not original:
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        return

    suffix = " (копия)"
    base_name = original.name or "Тариф"
    if len(base_name) + len(suffix) > MAX_TARIFF_NAME_LENGTH:
        base_name = base_name[: MAX_TARIFF_NAME_LENGTH - len(suffix)]
    new_name = f"{base_name}{suffix}"

    excluded = {"id", "name", "created_at", "updated_at", "sort_order"}
    dup_data = {
        column.name: getattr(original, column.name)
        for column in Tariff.__table__.columns
        if column.name not in excluded
    }
    dup_data["name"] = new_name

    new_tariff = await create_tariff(session, dup_data)

    text, markup = render_tariff_card(new_tariff)
    await callback.message.edit_text(
        text=menu_text("Тарифы", "📑 Тариф дублирован.", quote(f"{text}"), markup=markup), reply_markup=markup
    )


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("delete|")), IsAdminFilter())
async def confirm_tariff_deletion(callback: CallbackQuery, callback_data: AdminTariffCallback, session: AsyncSession):
    tariff_id = int(callback_data.action.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        return

    group_code = tariff.group_code

    if group_code == "gifts":
        gift_check = await session.execute(select(Gift).where(Gift.tariff_id == tariff_id).limit(1))
        if gift_check.scalar_one_or_none():
            result = await session.execute(select(Tariff).where(Tariff.group_code == "gifts", Tariff.id != tariff_id))
            other_tariffs = result.scalars().all()

            if not other_tariffs:
                await callback.message.edit_text(
                    menu_text(
                        "Тарифы",
                        "❌ Нельзя удалить тариф — он используется в подарках, а других тарифов в группе 'gifts' нет.",
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=BACK, callback_data=AdminTariffCallback(action=f"view|{tariff_id}").pack()
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
                menu_text(
                    "Тариф в подарках",
                    "Тариф используется в подарках — выберите замену перед удалением.",
                    markup=builder,
                ),
                reply_markup=builder,
            )
            return

    await callback.message.edit_text(
        menu_text("Тарифы", "⚠️ Удалить тариф?"),
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
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        return

    group_code = tariff.group_code

    await session.execute(delete(Tariff).where(Tariff.id == tariff_id))

    result = await session.execute(select(Tariff).where(Tariff.group_code == group_code))
    remaining_tariffs = result.scalars().all()
    if not remaining_tariffs:
        await session.execute(update(Server).where(Server.tariff_group == group_code).values(tariff_group=None))

    await callback.message.edit_text(
        menu_text("Тарифы", "🗑 Тариф удалён. Все подарки обновлены.", markup=build_tariff_menu_kb()),
        reply_markup=build_tariff_menu_kb(),
    )


@router.callback_query(F.data.startswith("confirm_delete_tariff|"), IsAdminFilter())
async def delete_tariff(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split("|", 1)[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        return

    group_code = tariff.group_code

    await session.execute(update(Key).where(Key.tariff_id == tariff_id).values(tariff_id=None))
    await session.execute(delete(Tariff).where(Tariff.id == tariff_id))

    result = await session.execute(select(Tariff).where(Tariff.group_code == group_code))
    remaining_tariffs = result.scalars().all()

    if not remaining_tariffs:
        await session.execute(update(Server).where(Server.tariff_group == group_code).values(tariff_group=None))

    await callback.message.edit_text(
        menu_text("Тарифы", "🗑 Тариф удалён.", markup=build_tariff_menu_kb()),
        reply_markup=build_tariff_menu_kb(),
    )


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("edit|")), IsAdminFilter())
async def start_edit_tariff(callback: CallbackQuery, callback_data: AdminTariffCallback, state: FSMContext):
    tariff_id = int(callback_data.action.split("|")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(TariffEditState.choosing_field)
    await callback.message.edit_text(
        menu_text("Правка тарифа", "Выберите, что изменить.", markup=build_edit_tariff_fields_kb(tariff_id)),
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
            menu_text("Тарифы", "🔗 Установить флаг VLESS:"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Да (VLESS)", callback_data=f"set_vless|{tariff_id}|1"),
                        InlineKeyboardButton(text="❌ Нет", callback_data=f"set_vless|{tariff_id}|0"),
                    ],
                    [
                        InlineKeyboardButton(
                            text=BACK,
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
        "cooldown_days": "задержку между покупками в днях (0 — без задержки)",
        "description": "описание тарифа — что в него входит («-» — убрать)",
    }

    await callback.message.edit_text(
        menu_text("Тарифы", f"✏️ Новое значение для <b>{field_names.get(field, field)}</b>:", markup=build_cancel_kb()),
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
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        await state.clear()
        return

    tariff.vless = vless_flag
    tariff.updated_at = datetime.utcnow()
    await state.clear()

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)


@router.callback_query(F.data.startswith("tvis|"), IsAdminFilter())
async def show_visibility_menu(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split("|")[1])
    await state.clear()
    await callback.message.edit_text(
        menu_text("Тарифы", "👁 Кому показывать тариф:", markup=build_tariff_visibility_kb(tariff_id)),
        reply_markup=build_tariff_visibility_kb(tariff_id),
    )


@router.callback_query(F.data.startswith("tvisset|"), IsAdminFilter())
async def set_visibility(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, tid, mode, predicate = callback.data.split("|", 3)
    tariff_id = int(tid)

    if predicate == "active_count" and mode in ("only", "except"):
        await state.set_state(TariffEditState.visibility_count)
        await state.update_data(vis_tariff_id=tariff_id, vis_mode=mode)
        await callback.message.edit_text(
            menu_text("Тарифы", "Минимум активных подписок.", markup=build_cancel_kb()),
            reply_markup=build_cancel_kb(),
        )
        return

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        return
    tariff.visibility_rules = None if (mode == "all" or predicate == "none") else {"mode": mode, "predicate": predicate}
    tariff.updated_at = datetime.utcnow()

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)


@router.message(TariffEditState.visibility_count, IsAdminFilter())
async def apply_visibility_count(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text or not message.text.strip().isdigit() or int(message.text.strip()) <= 0:
        await message.answer(menu_text("Тарифы", "❌ Введите положительное число."))
        return
    data = await state.get_data()
    tariff_id = data.get("vis_tariff_id")
    mode = data.get("vis_mode")
    n = int(message.text.strip())
    await state.clear()

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()
    if not tariff:
        await message.answer(menu_text("Тарифы", "❌ Тариф не найден."))
        return
    tariff.visibility_rules = {"mode": mode, "predicate": "active_count", "min_count": n}
    tariff.updated_at = datetime.utcnow()

    text, markup = render_tariff_card(tariff)
    await message.answer(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)


@router.message(TariffEditState.editing_value, IsAdminFilter())
async def apply_edit(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tariff_id = data["tariff_id"]
    field = data["field"]
    value = message.text.strip()

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await message.answer(menu_text("Тарифы", "❌ Тариф не найден."))
        await state.clear()
        return

    if field == "name":
        is_valid, error_msg = validate_tariff_name(value)
        if not is_valid:
            await message.answer(
                menu_text("Тарифы", f"❌ {error_msg}", quote("Повторите ввод:"), markup=build_cancel_kb()),
                reply_markup=build_cancel_kb(),
            )
            return

    if field == "external_squad":
        if value in ("", "0", "-"):
            value = None
        setattr(tariff, field, value)
        tariff.updated_at = datetime.utcnow()
        await state.clear()

        text, markup = render_tariff_card(tariff)
        await message.answer(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)
        return

    if field == "description":
        tariff.description = None if value in ("", "0", "-") else value[:1000]
        tariff.updated_at = datetime.utcnow()
        await state.clear()

        text, markup = render_tariff_card(tariff)
        await message.answer(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)
        return

    if field in ["duration_days", "price_rub", "traffic_limit", "device_limit", "cooldown_days"]:
        try:
            num = int(value)
            if num < 0:
                raise ValueError
            if field in ["traffic_limit", "device_limit"]:
                value = num if num > 0 else None
            else:
                value = num
        except ValueError:
            await message.answer(menu_text("Тарифы", "❌ Введите корректное число."))
            return

    setattr(tariff, field, value)
    tariff.updated_at = datetime.utcnow()

    await state.clear()

    text, markup = render_tariff_card(tariff)
    if field in ["duration_days", "price_rub", "traffic_limit", "device_limit"]:
        from .tariff_utils import check_tariff_price_monotonicity, format_price_monotonicity_warning

        text += format_price_monotonicity_warning(await check_tariff_price_monotonicity(session, tariff))
    await message.answer(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)


@router.callback_query(F.data.startswith("toggle_active|"), IsAdminFilter())
async def toggle_tariff_status(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        return

    tariff.is_active = not tariff.is_active

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)


@router.callback_query(AdminTariffCallback.filter(F.action.startswith("create|")), IsAdminFilter())
async def start_tariff_creation_existing_group(
    callback: CallbackQuery, callback_data: AdminTariffCallback, state: FSMContext
):
    group_code = callback_data.action.split("|", 1)[1]
    await state.update_data(group_code=group_code)
    await state.set_state(TariffCreateState.name)
    await callback.message.edit_text(
        menu_text(
            "Тарифы",
            f"📦 Добавление нового тарифа в группу <code>{group_code}</code>",
            quote("📝 Введите <b>название тарифа</b>:"),
            markup=build_cancel_kb(),
        ),
        reply_markup=build_cancel_kb(),
    )


@router.callback_query(F.data.startswith("toggle_configurable|"), IsAdminFilter())
async def toggle_tariff_configurable(callback: CallbackQuery, session: AsyncSession):
    tariff_id = int(callback.data.split("|")[1])

    result = await session.execute(select(Tariff).where(Tariff.id == tariff_id))
    tariff = result.scalar_one_or_none()

    if not tariff:
        await callback.message.edit_text(menu_text("Тарифы", "❌ Тариф не найден."))
        return

    current = bool(tariff.configurable)
    tariff.configurable = not current
    tariff.updated_at = datetime.utcnow()

    text, markup = render_tariff_card(tariff)
    await callback.message.edit_text(text=menu_text("Тарифы", text, markup=markup), reply_markup=markup)
