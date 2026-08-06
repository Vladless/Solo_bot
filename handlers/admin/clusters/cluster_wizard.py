from typing import Any

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import check_unique_server_name, get_servers
from database.models import Server
from filters.admin import IsAdminFilter

from ..panel.headers import menu_text, quote, section
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

    text = menu_text(
        "Кластеры",
        "Пространство серверов, в котором создаётся подписка.",
        quote(
            "Нужен один сервер на клиента — держите в кластере ровно один сервер.",
            "Кластер удаляется сам, когда из него убран последний сервер.",
        ),
    )

    message = callback_query.message
    markup = build_clusters_editor_kb(servers)

    if message and message.text:
        await message.edit_text(text=text, reply_markup=markup)
    else:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(text=text, reply_markup=markup)


@router.callback_query(AdminClusterCallback.filter(F.action == "add"), IsAdminFilter())
async def handle_clusters_add(callback_query: CallbackQuery, state: FSMContext):
    text = menu_text(
        "Новый кластер",
        "Введите имя кластера.",
        quote(
            "Имя уникальное, не длиннее 12 символов.",
            "Например: <code>cluster1</code>, <code>us_east_1</code>",
        ),
    )

    await callback_query.message.edit_text(text=text, reply_markup=build_admin_back_kb("clusters"))

    await state.set_state(AdminClusterStates.waiting_for_cluster_name)


@router.message(AdminClusterStates.waiting_for_cluster_name, IsAdminFilter())
async def handle_cluster_name_input(message: Message, state: FSMContext):
    if not message.text:
        await message.answer(
            text=menu_text(
                "Кластеры",
                "❌ Имя не может быть пустым.",
                markup=build_admin_back_kb("clusters"),
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    if len(message.text) > 12:
        await message.answer(
            text=menu_text(
                "Кластеры",
                "❌ Максимум 12 символов.",
                markup=build_admin_back_kb("clusters"),
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    cluster_name = message.text.strip()
    await state.update_data(cluster_name=cluster_name)

    text = menu_text(
        "Имя сервера",
        f"Сервер для кластера <b>{cluster_name}</b>.",
        quote(
            "Удобно указывать локацию и номер.",
            "Например: <code>de1</code>, <code>fra1</code>, <code>fi2</code>",
        ),
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
            text=menu_text(
                "Кластеры",
                "❌ Имя не может быть пустым.",
                markup=build_admin_back_kb("clusters"),
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    server_name = message.text.strip()

    if len(server_name) > 12:
        await message.answer(
            text=menu_text(
                "Кластеры",
                "❌ Максимум 12 символов.",
                markup=build_admin_back_kb("clusters"),
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")

    if not await check_unique_server_name(session, server_name, cluster_name):
        await message.answer(
            text=menu_text(
                "Кластеры",
                "❌ Сервер с таким именем уже существует. Выберите другое имя.",
                markup=build_admin_back_kb("clusters"),
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    await state.update_data(server_name=server_name)

    text = menu_text(
        "API URL",
        f"Для сервера <b>{server_name}</b> в кластере <b>{cluster_name}</b>.",
        quote("Ссылку видно в адресной строке браузера при входе в панель."),
        quote(
            "3X-UI:\n<code>https://your-domain.com:port/panel_path/</code>",
            "Remnawave:\n<code>https://your-domain.com/api</code>",
        ),
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

    text = menu_text(
        "Subscription URL",
        f"Для сервера <b>{server_name}</b> в кластере <b>{cluster_name}</b>.",
        quote(
            "Формат: <code>https://your_domain:port/sub_path</code>",
            "На Remnawave введите <code>0</code>.",
        ),
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
        text=menu_text(
            "Inbound ID / Squads",
            f"Для сервера <b>{server_name}</b> в кластере <b>{cluster_name}</b>.",
            quote(
                "Remnawave — UUID сквада.",
                "3x-ui — числовой ID, например <code>1</code>.",
            ),
            markup=build_admin_back_kb("clusters"),
        ),
        reply_markup=build_admin_back_kb("clusters"),
    )
    await state.set_state(AdminClusterStates.waiting_for_inbound_id)


@router.message(AdminClusterStates.waiting_for_inbound_id, IsAdminFilter())
async def handle_inbound_id_input(message: Message, state: FSMContext):
    inbound_id = message.text.strip()
    await state.update_data(inbound_id=inbound_id)

    await message.answer(
        text=menu_text(
            "Тип панели",
            "Выберите панель этого сервера.",
            quote("⚠️ Часть функций Remnawave ещё в разработке, режим выбора стран поддержан ограниченно."),
            markup=build_panel_type_kb(),
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

    await callback_query.message.edit_text(
        text=menu_text(
            "Кластеры",
            f"✅ Сервер <b>{server_name}</b> добавлен в кластер <b>{cluster_name}</b>.",
            quote(f"Панель: {panel_type}"),
            markup=build_admin_back_kb("clusters"),
        ),
        reply_markup=build_admin_back_kb("clusters"),
    )
    await state.clear()


@router.callback_query(AdminServerCallback.filter(F.action == "add"), IsAdminFilter())
async def handle_add_server(callback_query: CallbackQuery, callback_data: AdminServerCallback, state: FSMContext):
    cluster_name = callback_data.data

    await state.update_data(cluster_name=cluster_name)

    text = menu_text(
        "Кластеры",
        f"Придумайте имя сервера для кластера <b>{cluster_name}</b>.",
        quote("В имени удобно указывать локацию и номер сервера."),
        section("💡 Пример", "de1, fra1, fi2"),
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )

    await state.set_state(AdminClusterStates.waiting_for_server_name)
