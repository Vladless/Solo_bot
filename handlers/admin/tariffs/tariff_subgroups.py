from datetime import datetime

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Tariff
from database.tariffs import (
    create_subgroup_hash,
    find_subgroup_by_hash,
    get_subgroup_description,
    get_tariffs,
    set_subgroup_description,
)
from filters.admin import IsAdminFilter
from settings.buttons import BACK

from ..panel.headers import menu_text, quote, section
from . import router
from .keyboard import AdminTariffCallback, build_tariff_menu_kb
from .tariff_states import SubgroupEditState, TariffSubgroupState
from .tariff_utils import tariff_to_dict, validate_subgroup_title


@router.callback_query(F.data.startswith("start_subgrouping|"), IsAdminFilter())
async def start_subgrouping(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    group_code = callback.data.split("|", 1)[1]

    tariffs = await get_tariffs(session, group_code=group_code)
    tariffs = [t for t in tariffs if not t.get("subgroup_title") or t.get("subgroup_title") == ""]

    if not tariffs:
        await callback.message.edit_text(
            menu_text(
                "Подгруппы",
                "❌ Нет доступных тарифов для группировки.",
                quote("Все тарифы уже находятся в подгруппах."),
            ),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=BACK, callback_data=AdminTariffCallback(action=f"group|{group_code}").pack()
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
        menu_text("Подгруппы", "Выберите тарифы, которые нужно объединить в подгруппу:", markup=builder.as_markup()),
        reply_markup=builder.as_markup(),
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
    flags={"popup": True},
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
        menu_text("Подгруппы", "📁 Название подгруппы.", markup=keyboard),
        reply_markup=keyboard,
    )


@router.message(TariffSubgroupState.entering_subgroup_title, IsAdminFilter())
async def apply_subgroup_title(message: Message, state: FSMContext, session: AsyncSession):
    title = message.text.strip()

    is_valid, error_msg = validate_subgroup_title(title)
    if not is_valid:
        await message.answer(
            menu_text("Подгруппы", f"❌ {error_msg}", quote("Повторите ввод:")),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_subgrouping")]]
            ),
        )
        return

    data = await state.get_data()
    selected_ids = data.get("selected_tariff_ids", [])

    if not selected_ids:
        await message.answer(menu_text("Подгруппы", "❌ Нет выбранных тарифов."))
        await state.clear()
        return

    await session.execute(
        update(Tariff).where(Tariff.id.in_(selected_ids)).values(subgroup_title=title, updated_at=datetime.utcnow())
    )
    await state.clear()

    await message.answer(
        menu_text(
            "Подгруппы",
            f"✅ {len(selected_ids)} тарифов сгруппированы в подгруппу: <b>{title}</b>.",
            markup=build_tariff_menu_kb(),
        ),
        reply_markup=build_tariff_menu_kb(),
    )


@router.callback_query(F.data == "cancel_subgrouping", IsAdminFilter())
async def cancel_subgrouping(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        menu_text("Подгруппы", "❌ Группировка в подгруппу отменена.", markup=build_tariff_menu_kb()),
        reply_markup=build_tariff_menu_kb(),
    )


@router.callback_query(F.data.startswith("view_subgroup|"), IsAdminFilter())
async def view_subgroup_tariffs(callback: CallbackQuery, session: AsyncSession):
    _, subgroup_hash, group_code = callback.data.split("|", 2)

    subgroup_title = await find_subgroup_by_hash(session, subgroup_hash, group_code)

    if not subgroup_title:
        await callback.message.edit_text(menu_text("Подгруппы", "❌ Подгруппа не найдена."))
        return

    tariffs = await get_tariffs(session, group_code=group_code)
    tariffs = [t for t in tariffs if t.get("subgroup_title") == subgroup_title]

    if not tariffs:
        await callback.message.edit_text(menu_text("Подгруппы", "❌ В этой подгруппе пока нет тарифов."))
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
        InlineKeyboardButton(text="📝 Переименовать", callback_data=f"rename_subgroup|{subgroup_hash}|{group_code}"),
        InlineKeyboardButton(text="📄 Текст", callback_data=f"subgroup_desc|{subgroup_hash}|{group_code}"),
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Состав", callback_data=f"edit_subgroup_tariffs|{subgroup_hash}|{group_code}"),
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_subgroup|{subgroup_hash}|{group_code}"),
    )

    builder.row(
        InlineKeyboardButton(
            text=BACK,
            callback_data=AdminTariffCallback(action=f"group|{group_code}").pack(),
        )
    )

    await callback.message.edit_text(
        menu_text("Подгруппа", f"<b>{subgroup_title}</b>", markup=builder.as_markup()),
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("subgroup_desc|"), IsAdminFilter())
async def start_edit_subgroup_description(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, subgroup_hash, group_code = callback.data.split("|", 2)
    subgroup_title = await find_subgroup_by_hash(session, subgroup_hash, group_code)
    if not subgroup_title:
        await callback.message.edit_text(menu_text("Подгруппы", "❌ Подгруппа не найдена."))
        return

    current = await get_subgroup_description(session, group_code, subgroup_title)
    current_text = (current or "— не задан —").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    await state.update_data(subgroup_title=subgroup_title, group_code=group_code, subgroup_hash=subgroup_hash)
    await state.set_state(SubgroupEditState.entering_description)
    await callback.message.edit_text(
        menu_text(
            "Текст подгруппы",
            f"<b>{subgroup_title}</b> — текст над списком тарифов.",
            section("📝 Сейчас", current_text),
            quote("Пришлите новый текст или «-», чтобы убрать."),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_subgroup|{subgroup_hash}|{group_code}")]
            ]
        ),
    )


@router.message(SubgroupEditState.entering_description, IsAdminFilter())
async def save_subgroup_description(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    group_code = data.get("group_code")
    subgroup_title = data.get("subgroup_title")
    subgroup_hash = data.get("subgroup_hash")

    value = (message.text or "").strip()
    description = None if value in ("", "-", "0") else value[:1000]
    await set_subgroup_description(session, group_code, subgroup_title, description)
    await state.clear()

    await message.answer(
        menu_text("Подгруппы", "✅ Текст подгруппы обновлён." if description else "✅ Текст подгруппы убран."),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=BACK, callback_data=f"view_subgroup|{subgroup_hash}|{group_code}")]
            ]
        ),
    )


@router.callback_query(F.data.startswith("rename_subgroup|"), IsAdminFilter())
async def start_rename_subgroup(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, subgroup_hash, group_code = callback.data.split("|", 2)

    subgroup_title = await find_subgroup_by_hash(session, subgroup_hash, group_code)

    if not subgroup_title:
        await callback.message.edit_text(menu_text("Подгруппы", "❌ Подгруппа не найдена."))
        return

    await state.update_data(
        subgroup_title=subgroup_title,
        group_code=group_code,
        subgroup_hash=subgroup_hash,
    )

    await state.set_state(SubgroupEditState.entering_new_title)
    await callback.message.edit_text(
        menu_text("Переименование", f"Новое название для <b>{subgroup_title}</b>.", quote("Или нажмите «Отмена».")),
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
            menu_text("Подгруппы", f"❌ {error_msg}", quote("Повторите ввод:")),
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
    await state.clear()

    create_subgroup_hash(new_title, group_code)

    await message.answer(
        menu_text("Подгруппы", f"✅ Подгруппа <b>{old_title}</b> переименована в <b>{new_title}</b>."),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=BACK, callback_data=AdminTariffCallback(action=f"group|{group_code}").pack()
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
        await callback.message.edit_text(menu_text("Подгруппы", "❌ Подгруппа не найдена."))
        return

    await state.update_data(
        subgroup_title=subgroup_title,
        group_code=group_code,
        subgroup_hash=subgroup_hash,
    )
    await state.set_state(SubgroupEditState.confirming_deletion)

    await callback.message.edit_text(
        menu_text(
            "Удаление подгруппы",
            f"Удалить подгруппу <b>{subgroup_title}</b>?",
            quote("Тарифы останутся, но потеряют привязку к подгруппе."),
        ),
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
    await state.clear()

    await callback.message.edit_text(
        menu_text("Подгруппы", f"✅ Подгруппа <b>{subgroup_title}</b> удалена."),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=BACK, callback_data=AdminTariffCallback(action=f"group|{group_code}").pack()
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
        await callback.message.edit_text(menu_text("Подгруппы", "❌ Подгруппа не найдена."))
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
            menu_text("Подгруппы", "❌ Нет доступных тарифов для редактирования."),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=BACK, callback_data=f"view_subgroup|{subgroup_hash}|{group_code}")]
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
        menu_text(
            "Состав подгруппы",
            f"<b>{subgroup_title}</b>",
            quote("Нажмите на тариф, чтобы добавить его или убрать. Галочкой отмечены те, что уже внутри."),
            markup=builder.as_markup(),
        ),
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

    await state.clear()

    if not selected_tariff_ids:
        await callback.message.edit_text(
            menu_text("Подгруппы", f"✅ Подгруппа <b>{subgroup_title}</b> была расформирована."),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ К группе",
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
        menu_text("Подгруппа обновлена", f"<b>{subgroup_title}</b>", section("🔄 Изменения", *changes_text)),
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
