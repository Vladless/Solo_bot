from aiogram import F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from database.servers import (
    get_available_clusters,
    get_server_by_name,
    update_server_cluster,
    update_server_field,
    update_server_name_with_keys,
)
from filters.admin import IsAdminFilter

from ..panel.headers import menu_text, quote
from ..panel.keyboard import build_admin_back_kb
from .keyboard import (
    AdminServerCallback,
    build_cancel_edit_kb,
    build_cluster_selection_kb,
    build_edit_server_fields_kb,
    build_panel_type_selection_kb,
)
from .server_states import ServerEditState, router


@router.callback_query(F.data.startswith("edit_server|"), IsAdminFilter())
async def start_edit_server(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    server_name = callback.data.split("|")[1]

    await state.clear()

    server_data = await get_server_by_name(session, server_name)
    if not server_data:
        await callback.message.edit_text(menu_text("Сервер", "❌ Сервер не найден."))
        return

    await callback.message.edit_text(
        menu_text(
            "Сервер",
            f"<b>{server_name}</b>",
            quote("Выберите поле для редактирования:"),
            markup=build_edit_server_fields_kb(server_name, server_data),
        ),
        reply_markup=build_edit_server_fields_kb(server_name, server_data),
    )


@router.callback_query(F.data.startswith("edit_server_field|"), IsAdminFilter())
async def ask_new_field_value(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, server_name, field = callback.data.split("|")

    if field == "cluster_name":
        clusters = await get_available_clusters(session)
        await callback.message.edit_text(
            menu_text("Сервер", f"Кластер для <b>{server_name}</b>."),
            reply_markup=build_cluster_selection_kb(server_name, clusters),
        )
        return

    await state.update_data(server_name=server_name, field=field)
    await state.set_state(ServerEditState.editing_value)

    field_names = {
        "server_name": "имя сервера",
        "api_url": "API URL",
        "subscription_url": "Subscription URL",
        "inbound_id": "Inbound ID/Squads",
    }

    await callback.message.edit_text(
        menu_text(
            "Сервер",
            f"✏️ Новое значение для <b>{field_names.get(field, field)}</b>:",
            markup=build_cancel_edit_kb(server_name),
        ),
        reply_markup=build_cancel_edit_kb(server_name),
    )


@router.callback_query(F.data.startswith("select_panel_type|"), IsAdminFilter())
async def select_panel_type(callback: CallbackQuery):
    server_name = callback.data.split("|")[1]

    await callback.message.edit_text(
        menu_text(
            "Сервер",
            f"Тип панели для <b>{server_name}</b>.",
            markup=build_panel_type_selection_kb(server_name),
        ),
        reply_markup=build_panel_type_selection_kb(server_name),
    )


@router.callback_query(F.data.startswith("set_panel_type|"), IsAdminFilter())
async def set_panel_type(callback: CallbackQuery, session: AsyncSession):
    _, server_name, panel_type = callback.data.split("|")

    success = await update_server_field(session, server_name, "panel_type", panel_type)
    if success:
        await callback.message.edit_text(
            menu_text("Сервер", f"✅ Тип панели сервера {server_name} изменен на {panel_type}"),
            reply_markup=InlineKeyboardBuilder()
            .button(
                text="⬅️ Назад к серверу",
                callback_data=AdminServerCallback(action="manage", data=server_name).pack(),
            )
            .as_markup(),
        )
    else:
        await callback.message.edit_text(menu_text("Сервер", "❌ Ошибка при изменении типа панели"))


@router.callback_query(F.data.startswith("set_cluster|"), IsAdminFilter())
async def set_cluster(callback: CallbackQuery, session: AsyncSession):
    _, server_name, new_cluster = callback.data.split("|")

    success = await update_server_cluster(session, server_name, new_cluster)
    if success:
        await callback.message.edit_text(
            menu_text("Сервер", f"✅ Кластер сервера {server_name} изменен на {new_cluster}"),
            reply_markup=InlineKeyboardBuilder()
            .button(
                text="⬅️ Назад к серверу",
                callback_data=AdminServerCallback(action="manage", data=server_name).pack(),
            )
            .as_markup(),
        )
    else:
        await callback.message.edit_text(menu_text("Сервер", "❌ Ошибка при изменении кластера"))


@router.message(ServerEditState.editing_value, IsAdminFilter())
async def apply_field_edit(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    server_name = data["server_name"]
    field = data["field"]
    value = message.text.strip()

    if field == "server_name":
        if len(value) > 12:
            await message.answer(
                text=menu_text(
                    "Сервер",
                    "❌ Максимум 12 символов.",
                    markup=build_admin_back_kb("clusters"),
                ),
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        success = await update_server_name_with_keys(session, server_name, value)
        if success:
            server_name = value
        else:
            await message.answer(
                menu_text("Имя сервера", "Не удалось изменить.", quote("Возможно, такое имя уже занято."))
            )
            return
    else:
        success = await update_server_field(session, server_name, field, value)

    if success:
        field_names = {
            "server_name": "имя сервера",
            "api_url": "API URL",
            "subscription_url": "Subscription URL",
            "inbound_id": "Inbound ID/Squads",
        }

        await message.answer(
            menu_text("Сервер", f"✅ {field_names.get(field, field).capitalize()} изменено"),
            reply_markup=InlineKeyboardBuilder()
            .button(
                text="⬅️ Назад к серверу",
                callback_data=AdminServerCallback(action="manage", data=server_name).pack(),
            )
            .as_markup(),
        )
    else:
        await message.answer(menu_text("Сервер", "❌ Ошибка при изменении поля"))

    await state.clear()
