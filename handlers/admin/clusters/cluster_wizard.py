from typing import Any

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import check_unique_server_name, get_servers
from database.models import Server
from filters.admin import IsAdminFilter

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .base import AdminClusterStates, router
from .keyboard import (
    AdminClusterCallback,
    AdminServerCallback,
    build_clusters_editor_kb,
    build_panel_type_kb,
)


@router.callback_query(
    AdminPanelCallback.filter(F.action == "clusters"),
    IsAdminFilter(),
)
async def handle_servers(callback_query: CallbackQuery, session: AsyncSession):
    servers = await get_servers(session, include_enabled=True)

    text = (
        "<b>🔧 Управление кластерами</b>\n\n"
        "<blockquote>"
        "🌐 <b>Кластеры</b> — это пространство серверов, в пределах которого создается подписка.\n"
        "💡 Если вы хотите выдавать по 1 серверу, то добавьте всего 1 сервер в кластер."
        "</blockquote>\n\n"
        "<i>⚠️ <b>Важно:</b> Кластеры удаляются автоматически, если удалить все серверы внутри них.</i>\n\n"
    )

    message = callback_query.message
    markup = build_clusters_editor_kb(servers)

    if message and message.text:
        await message.edit_text(text=text, reply_markup=markup)
    elif message and message.caption:
        await message.edit_caption(caption=text, reply_markup=markup)
    else:
        await message.answer(text=text, reply_markup=markup)


@router.callback_query(AdminClusterCallback.filter(F.action == "add"), IsAdminFilter())
async def handle_clusters_add(callback_query: CallbackQuery, state: FSMContext):
    text = (
        "🔧 <b>Введите имя нового кластера:</b>\n\n"
        "<b>Имя должно быть уникальным!</b>\n"
        "<b>Имя не должно превышать 12 символов!</b>\n\n"
        "<i>Пример:</i> <code>cluster1</code> или <code>us_east_1</code>"
    )

    await callback_query.message.edit_text(text=text, reply_markup=build_admin_back_kb("clusters"))

    await state.set_state(AdminClusterStates.waiting_for_cluster_name)


@router.message(AdminClusterStates.waiting_for_cluster_name, IsAdminFilter())
async def handle_cluster_name_input(message: Message, state: FSMContext):
    if not message.text:
        await message.answer(
            text="❌ Имя кластера не может быть пустым! Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    if len(message.text) > 12:
        await message.answer(
            text="❌ Имя кластера не должно превышать 12 символов! Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    cluster_name = message.text.strip()
    await state.update_data(cluster_name=cluster_name)

    text = (
        f"<b>Введите имя сервера для кластера {cluster_name}:</b>\n\n"
        "Рекомендуется указать локацию и номер сервера в имени.\n\n"
        "<i>Пример:</i> <code>de1</code>, <code>fra1</code>, <code>fi2</code>"
    )

    await message.answer(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )

    await state.set_state(AdminClusterStates.waiting_for_server_name)


@router.message(AdminClusterStates.waiting_for_server_name, IsAdminFilter())
async def handle_server_name_input(message: Message, state: FSMContext, session: Any):
    if not message.text:
        await message.answer(
            text="❌ Имя сервера не может быть пустым. Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    server_name = message.text.strip()

    if len(server_name) > 12:
        await message.answer(
            text="❌ Имя сервера не должно превышать 12 символов. Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")

    if not await check_unique_server_name(session, server_name, cluster_name):
        await message.answer(
            text="❌ Сервер с таким именем уже существует. Пожалуйста, выберите другое имя.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    await state.update_data(server_name=server_name)

    text = (
        f"<b>Введите API URL для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "🔍 Ссылку можно найти в адресной строке браузера при входе в панель управления сервером.\n\n"
        "ℹ️ <b>Формат для 3X-UI:</b>\n"
        "<code>https://your-domain.com:port/panel_path/</code>\n\n"
        "ℹ️ <b>Формат для Remnawave:</b>\n"
        "<code>https://your-domain.com/api</code>"
    )

    await message.answer(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )

    await state.set_state(AdminClusterStates.waiting_for_api_url)


@router.message(AdminClusterStates.waiting_for_api_url, IsAdminFilter())
async def handle_api_url_input(message: Message, state: FSMContext):
    api_url = message.text.strip().rstrip("/")

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")

    await state.update_data(api_url=api_url)

    text = (
        f"<b>Введите subscription_url для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "Если вы используете Remnawave — введите <code>0</code>\n\n"
        "<i>Формат:</i> <code>https://your_domain:port/sub_path</code>"
    )

    await message.answer(text=text, reply_markup=build_admin_back_kb("clusters"))
    await state.set_state(AdminClusterStates.waiting_for_subscription_url)


@router.message(AdminClusterStates.waiting_for_subscription_url, IsAdminFilter())
async def handle_subscription_url_input(message: Message, state: FSMContext):
    raw = message.text.strip()
    subscription_url = None if raw == "0" else raw.rstrip("/")

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")

    await state.update_data(subscription_url=subscription_url)

    await message.answer(
        text=f"<b>Введите inbound_id/Squads для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        f"Для Remnawave это UUID Squads, для 3x-ui — просто ID (например, <code>1</code>).",
        reply_markup=build_admin_back_kb("clusters"),
    )
    await state.set_state(AdminClusterStates.waiting_for_inbound_id)


@router.message(AdminClusterStates.waiting_for_inbound_id, IsAdminFilter())
async def handle_inbound_id_input(message: Message, state: FSMContext):
    inbound_id = message.text.strip()
    await state.update_data(inbound_id=inbound_id)

    await message.answer(
        text=(
            "🧩 <b>Выберите тип панели для этого сервера:</b>\n\n"
            "⚠️ <b>Внимание:</b> Некоторые функции <b>Remnawave</b> находятся в разработке.\n"
            "Поддержка режима выбора стран — <b>ограничена</b>."
        ),
        reply_markup=build_panel_type_kb(),
    )


@router.callback_query(
    AdminClusterCallback.filter(F.action.in_(["panel_3xui", "panel_remnawave"])),
    IsAdminFilter(),
)
async def handle_panel_type_selection(
    callback_query: CallbackQuery,
    callback_data: AdminClusterCallback,
    state: FSMContext,
    session: AsyncSession,
):
    panel_type = "3x-ui" if callback_data.action == "panel_3xui" else "remnawave"

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    api_url = user_data.get("api_url")
    subscription_url = user_data.get("subscription_url")
    inbound_id = user_data.get("inbound_id")

    result = await session.execute(select(Server.tariff_group).where(Server.cluster_name == cluster_name).limit(1))
    row = result.first()
    tariff_group = row[0] if row else None

    new_server = Server(
        cluster_name=cluster_name,
        server_name=server_name,
        api_url=api_url,
        subscription_url=subscription_url,
        inbound_id=inbound_id,
        panel_type=panel_type,
        tariff_group=tariff_group,
    )

    session.add(new_server)
    await session.commit()

    await callback_query.message.edit_text(
        text=f"✅ Сервер <b>{server_name}</b> с панелью <b>{panel_type}</b> успешно добавлен в кластер <b>{cluster_name}</b>!",
        reply_markup=build_admin_back_kb("clusters"),
    )
    await state.clear()


@router.callback_query(AdminServerCallback.filter(F.action == "add"), IsAdminFilter())
async def handle_add_server(callback_query: CallbackQuery, callback_data: AdminServerCallback, state: FSMContext):
    cluster_name = callback_data.data

    await state.update_data(cluster_name=cluster_name)

    text = (
        f"<b>Введите имя сервера для кластера {cluster_name}:</b>\n\n"
        "Рекомендуется указать локацию и номер сервера в имени.\n\n"
        "<i>Пример:</i> <code>de1</code>, <code>fra1</code>, <code>fi2</code>"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )

    await state.set_state(AdminClusterStates.waiting_for_server_name)
