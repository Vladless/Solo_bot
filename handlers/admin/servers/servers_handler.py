from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_servers
from database.models import Key, Server
from database.servers import (
    get_available_clusters,
    get_server_by_name,
    update_server_cluster,
    update_server_field,
    update_server_name_with_keys,
)
from filters.admin import IsAdminFilter
from handlers.buttons import BACK

from ..panel.keyboard import build_admin_back_kb
from .keyboard import (
    AdminServerCallback,
    build_cancel_edit_kb,
    build_cluster_selection_kb,
    build_edit_server_fields_kb,
    build_manage_server_kb,
    build_panel_type_selection_kb,
)


router = Router()


class ServerLimitState(StatesGroup):
    waiting_for_limit = State()


class ServerEditState(StatesGroup):
    choosing_field = State()
    editing_value = State()


@router.callback_query(AdminServerCallback.filter(F.action == "manage"), IsAdminFilter())
async def handle_server_manage(
    callback_query: CallbackQuery,
    callback_data: AdminServerCallback,
    session: AsyncSession,
):
    server_name = callback_data.data
    servers = await get_servers(session=session, include_enabled=True)

    cluster_name, server = next(
        ((c, s) for c, cs in servers.items() for s in cs if s["server_name"] == server_name),
        (None, None),
    )

    if server:
        api_url = server["api_url"]
        subscription_url = server["subscription_url"]
        inbound_id = server["inbound_id"]
        panel_type = server.get("panel_type") or "не указан"
        max_keys = server.get("max_keys")
        limit_display = f"{max_keys}" if max_keys else "не задан"

        result = await session.execute(select(func.count()).where(Key.server_id == server_name))
        subscription_count = result.scalar() or 0

        text = (
            f"<b>🔧 Информация о сервере {server_name}:</b>\n"
            f"<blockquote>"
            f"🗂 Кластер: <b>{cluster_name}</b>\n"
            f"📡 API URL: <b>{api_url}</b>\n"
        )

        if subscription_url:
            text += f"🌐 Subscription URL: <b>{subscription_url}</b>\n"

        text += (
            f"🔑 Inbound ID/Squads: <b>{inbound_id}</b>\n"
            f"⚙️ Тип панели: <b>{panel_type}</b>\n"
            f"📈 Лимит ключей: <b>{limit_display}</b>\n"
        )

        if subscription_count > 0:
            text += f"🔑 Подписок на сервере: <b>{subscription_count}</b>\n"

        text += "</blockquote>"

        await callback_query.message.edit_text(
            text=text,
            reply_markup=build_manage_server_kb(server_name, cluster_name, enabled=server.get("enabled", True)),
        )
    else:
        await callback_query.message.edit_text(text="❌ Сервер не найден.")


@router.callback_query(AdminServerCallback.filter(F.action == "delete"), IsAdminFilter())
async def process_callback_delete_server(
    callback_query: CallbackQuery,
    callback_data: AdminServerCallback,
    state: FSMContext,
    session: AsyncSession,
):
    from ..clusters.clusters_handler import AdminClusterStates

    server_name = callback_data.data

    servers_dict = await get_servers(session, include_enabled=True)
    cluster_name = None
    for c_name, server_list in servers_dict.items():
        if any(s["server_name"] == server_name for s in server_list):
            cluster_name = c_name
            break

    if not cluster_name:
        await callback_query.message.edit_text(
            text=f"❌ Не удалось найти кластер для сервера '{server_name}'.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    stmt_keys_count = select(func.count()).where(Key.server_id == server_name)
    result = await session.execute(stmt_keys_count)
    keys_count = result.scalar_one()

    if keys_count > 0:
        await state.update_data(server_name=server_name, cluster_name=cluster_name)

        subq = select(func.count()).where(Key.server_id == Server.server_name).correlate(Server).scalar_subquery()

        stmt_all_servers = select(Server.server_name, subq.label("key_count")).where(Server.server_name != server_name)
        result = await session.execute(stmt_all_servers)
        all_servers = result.all()

        if all_servers:
            builder = InlineKeyboardBuilder()
            for s_name, key_count in all_servers:
                callback_data = f"transfer_to_server|{s_name}|{server_name}"
                if len(callback_data.encode("utf-8")) > 64:
                    await callback_query.message.edit_text(
                        text=f"❌ Ошибка: название сервера '{s_name}' слишком длинное.\n\n"
                        f"Пожалуйста, переименуйте сервер в более короткое название и попробуйте снова.",
                        reply_markup=build_admin_back_kb("clusters"),
                    )
                    return

                builder.row(
                    InlineKeyboardButton(
                        text=f"{s_name} ({key_count})",
                        callback_data=callback_data,
                    )
                )
            builder.row(
                InlineKeyboardButton(
                    text=BACK,
                    callback_data=AdminServerCallback(action="manage", data=server_name).pack(),
                )
            )

            await callback_query.message.edit_text(
                text=f"⚠️ На сервере '{server_name}' есть {keys_count} ключей. Выберите сервер для переноса ключей:",
                reply_markup=builder.as_markup(),
            )
            await state.set_state(AdminClusterStates.waiting_for_server_transfer)
            return

    stmt_remaining = select(func.count()).where(
        (Server.cluster_name == cluster_name) & (Server.server_name != server_name)
    )
    result = await session.execute(stmt_remaining)
    remaining_servers = result.scalar_one()

    if remaining_servers == 0:
        stmt_other_clusters = select(Server.cluster_name).distinct().where(Server.cluster_name != cluster_name)
        result = await session.execute(stmt_other_clusters)
        other_clusters = result.scalars().all()

        if other_clusters:
            stmt_cluster_keys = select(func.count()).where(Key.server_id == cluster_name)
            result = await session.execute(stmt_cluster_keys)
            cluster_keys_count = result.scalar_one()

            if cluster_keys_count > 0:
                await state.update_data(server_name=server_name, cluster_name=cluster_name)

                subq_cluster = (
                    select(func.count()).where(Key.server_id == Server.cluster_name).correlate(Server).scalar_subquery()
                )

                stmt_all_clusters = (
                    select(Server.cluster_name, subq_cluster.label("key_count"))
                    .where(Server.cluster_name != cluster_name)
                    .group_by(Server.cluster_name)
                )
                result = await session.execute(stmt_all_clusters)
                all_clusters = result.all()

                builder = InlineKeyboardBuilder()
                for cl_name, key_count in all_clusters:
                    callback_data = f"transfer_to_cluster|{cl_name}|{cluster_name}|{server_name}"
                    if len(callback_data.encode("utf-8")) > 64:
                        await callback_query.message.edit_text(
                            text=f"❌ Ошибка: название сервера '{server_name}' или кластера '{cl_name}' слишком длинное.\n\n"
                            f"Пожалуйста, переименуйте сервер в более короткое название и попробуйте снова.",
                            reply_markup=build_admin_back_kb("clusters"),
                        )
                        return

                    builder.row(
                        InlineKeyboardButton(
                            text=f"{cl_name} ({key_count})",
                            callback_data=callback_data,
                        )
                    )
                builder.row(
                    InlineKeyboardButton(
                        text=BACK,
                        callback_data=AdminServerCallback(action="manage", data=server_name).pack(),
                    )
                )

                await callback_query.message.edit_text(
                    text=f"⚠️ Это последний сервер в кластере '{cluster_name}'. На кластере есть {cluster_keys_count} ключей. Выберите кластер для переноса ключей:",
                    reply_markup=builder.as_markup(),
                )
                await state.set_state(AdminClusterStates.waiting_for_cluster_transfer)
                return

        stmt_delete = delete(Server).where((Server.cluster_name == cluster_name) & (Server.server_name == server_name))
        await session.execute(stmt_delete)
        await session.commit()
        await callback_query.message.edit_text(
            text=f"✅ Сервер '{server_name}' удален. Кластер '{cluster_name}' также удален, так как в нем не осталось серверов.",
            reply_markup=build_admin_back_kb("clusters"),
        )
    else:
        stmt_delete = delete(Server).where((Server.cluster_name == cluster_name) & (Server.server_name == server_name))
        await session.execute(stmt_delete)
        await session.commit()
        await callback_query.message.edit_text(
            text=f"✅ Сервер '{server_name}' удален.",
            reply_markup=build_admin_back_kb("clusters"),
        )


@router.callback_query(AdminServerCallback.filter(F.action.in_(["enable", "disable"])), IsAdminFilter())
async def toggle_server_enabled(
    callback_query: CallbackQuery,
    callback_data: AdminServerCallback,
    session: AsyncSession,
):
    server_name = callback_data.data
    action = callback_data.action

    new_status = action == "enable"

    await session.execute(update(Server).where(Server.server_name == server_name).values(enabled=new_status))
    await session.commit()

    servers = await get_servers(session=session, include_enabled=True)

    cluster_name, server = next(
        ((c, s) for c, cs in servers.items() for s in cs if s["server_name"] == server_name),
        (None, None),
    )

    if not server:
        await callback_query.message.edit_text("❌ Сервер не найден.")
        return

    max_keys = server.get("max_keys")
    limit_display = f"{max_keys}" if max_keys else "не задан"

    text = (
        f"<b>🔧 Информация о сервере {server_name}:</b>\n\n"
        f"<b>📡 API URL:</b> {server['api_url']}\n"
        f"<b>🌐 Subscription URL:</b> {server['subscription_url']}\n"
        f"<b>🔑 Inbound ID/Squads:</b> {server['inbound_id']}\n"
        f"<b>📈 Лимит ключей:</b> {limit_display}"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_manage_server_kb(server_name, cluster_name, enabled=new_status),
    )


@router.callback_query(AdminServerCallback.filter(F.action == "set_limit"), IsAdminFilter())
async def ask_server_limit(callback_query: CallbackQuery, callback_data: AdminServerCallback, state: FSMContext):
    server_name = callback_data.data
    await state.set_state(ServerLimitState.waiting_for_limit)
    await state.update_data(server_name=server_name)
    await callback_query.message.edit_text(
        f"Введите лимит ключей для сервера <b>{server_name}</b> (целое число, 0 — без лимита):",
    )


@router.message(ServerLimitState.waiting_for_limit, IsAdminFilter())
async def save_server_limit(message: types.Message, state: FSMContext, session: AsyncSession):
    try:
        limit = int(message.text.strip())
        if limit < 0:
            raise ValueError

        data = await state.get_data()
        server_name = data["server_name"]

        new_value = limit if limit > 0 else None

        await session.execute(update(Server).where(Server.server_name == server_name).values(max_keys=new_value))
        await session.commit()

        servers = await get_servers(session=session, include_enabled=True)
        cluster_name, server = next(
            ((c, s) for c, cs in servers.items() for s in cs if s["server_name"] == server_name),
            (None, None),
        )

        if not server:
            await message.answer("❌ Сервер не найден.")
            await state.clear()
            return

        max_keys = server.get("max_keys")
        limit_display = f"{max_keys}" if max_keys is not None else "не задан"

        text = (
            f"<b>🔧 Информация о сервере {server_name}:</b>\n\n"
            f"<b>📡 API URL:</b> {server['api_url']}\n"
            f"<b>🌐 Subscription URL:</b> {server['subscription_url']}\n"
            f"<b>🔑 Inbound ID/Squads:</b> {server['inbound_id']}\n"
            f"<b>📈 Лимит ключей:</b> {limit_display}"
        )

        await message.answer(
            text,
            reply_markup=build_manage_server_kb(server_name, cluster_name, enabled=server.get("enabled", True)),
        )
        await state.clear()

    except ValueError:
        await message.answer("❌ Введите корректное целое число (0 = без лимита)")


@router.callback_query(F.data.startswith("edit_server|"), IsAdminFilter())
async def start_edit_server(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    server_name = callback.data.split("|")[1]

    await state.clear()

    server_data = await get_server_by_name(session, server_name)
    if not server_data:
        await callback.message.edit_text("❌ Сервер не найден.")
        return

    await callback.message.edit_text(
        f"<b>✏️ Редактирование сервера: {server_name}</b>\n\nВыберите поле для редактирования:",
        reply_markup=build_edit_server_fields_kb(server_name, server_data),
    )


@router.callback_query(F.data.startswith("edit_server_field|"), IsAdminFilter())
async def ask_new_field_value(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    _, server_name, field = callback.data.split("|")

    if field == "cluster_name":
        clusters = await get_available_clusters(session)
        await callback.message.edit_text(
            f"<b>🗂 Выберите кластер для сервера {server_name}:</b>",
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
        f"✏️ Введите новое значение для <b>{field_names.get(field, field)}</b>:",
        reply_markup=build_cancel_edit_kb(server_name),
    )


@router.callback_query(F.data.startswith("select_panel_type|"), IsAdminFilter())
async def select_panel_type(callback: CallbackQuery):
    server_name = callback.data.split("|")[1]

    await callback.message.edit_text(
        f"<b>⚙️ Выберите тип панели для сервера {server_name}:</b>",
        reply_markup=build_panel_type_selection_kb(server_name),
    )


@router.callback_query(F.data.startswith("set_panel_type|"), IsAdminFilter())
async def set_panel_type(callback: CallbackQuery, session: AsyncSession):
    _, server_name, panel_type = callback.data.split("|")

    success = await update_server_field(session, server_name, "panel_type", panel_type)
    if success:
        await callback.message.edit_text(
            f"✅ Тип панели сервера {server_name} изменен на {panel_type}",
            reply_markup=InlineKeyboardBuilder()
            .button(
                text="⬅️ Назад к серверу", callback_data=AdminServerCallback(action="manage", data=server_name).pack()
            )
            .as_markup(),
        )
    else:
        await callback.message.edit_text("❌ Ошибка при изменении типа панели")


@router.callback_query(F.data.startswith("set_cluster|"), IsAdminFilter())
async def set_cluster(callback: CallbackQuery, session: AsyncSession):
    _, server_name, new_cluster = callback.data.split("|")

    success = await update_server_cluster(session, server_name, new_cluster)
    if success:
        await callback.message.edit_text(
            f"✅ Кластер сервера {server_name} изменен на {new_cluster}",
            reply_markup=InlineKeyboardBuilder()
            .button(
                text="⬅️ Назад к серверу", callback_data=AdminServerCallback(action="manage", data=server_name).pack()
            )
            .as_markup(),
        )
    else:
        await callback.message.edit_text("❌ Ошибка при изменении кластера")


@router.message(ServerEditState.editing_value, IsAdminFilter())
async def apply_field_edit(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    server_name = data["server_name"]
    field = data["field"]
    value = message.text.strip()

    if field == "server_name":
        if len(value) > 12:
            await message.answer(
                text="❌ Имя сервера не должно превышать 12 символов. Попробуйте снова.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        success = await update_server_name_with_keys(session, server_name, value)
        if success:
            server_name = value
        else:
            await message.answer("❌ Ошибка при изменении имени сервера. Возможно, такое имя уже существует.")
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
            f"✅ {field_names.get(field, field).capitalize()} изменено",
            reply_markup=InlineKeyboardBuilder()
            .button(
                text="⬅️ Назад к серверу", callback_data=AdminServerCallback(action="manage", data=server_name).pack()
            )
            .as_markup(),
        )
    else:
        await message.answer("❌ Ошибка при изменении поля")

    await state.clear()
