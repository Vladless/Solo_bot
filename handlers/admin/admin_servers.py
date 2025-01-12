import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from py3xui import AsyncApi

from backup import create_backup_and_send_to_admins
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL
from database import check_unique_server_name, get_servers_from_db
from filters.admin import IsAdminFilter
from keyboards.admin.panel_kb import AdminPanelCallback, build_admin_back_kb
from keyboards.admin.servers_kb import build_cancel_kb, build_manage_server_kb, \
    build_delete_server_kb, \
    build_manage_cluster_kb, build_clusters_editor_kb, AdminServerEditorCallback

router = Router()


class AdminServersEditor(StatesGroup):
    waiting_for_cluster_name = State()
    waiting_for_api_url = State()
    waiting_for_inbound_id = State()
    waiting_for_server_name = State()
    waiting_for_subscription_url = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "servers_editor"),
    IsAdminFilter(),
)
async def handle_servers_editor(
        callback_query: types.CallbackQuery
):
    servers = await get_servers_from_db()

    text = (
        "<b>🔧 Управление кластерами</b>\n\n"
        "<i>📌 Здесь вы можете добавить новый кластер.</i>\n\n"
        "<i>🌐 <b>Кластеры</b> — это пространство серверов, в пределах которого создается подписка.</i>\n"
        "💡 Если вы хотите выдавать по 1 серверу, то добавьте всего 1 сервер в кластер.\n\n"
        "<i>⚠️ <b>Важно:</b> Кластеры удаляются автоматически, если удалить все серверы внутри них.</i>\n\n"
    )

    await callback_query.message.answer(
        text=text,
        parse_mode="HTML",
        reply_markup=build_clusters_editor_kb(servers),
    )


@router.callback_query(
    AdminPanelCallback.filter(F.action == "clusters_add"),
    IsAdminFilter(),
)
async def handle_add_cluster(
        callback_query: types.CallbackQuery,
        state: FSMContext
):
    text = (
        "🔧 <b>Введите имя нового кластера:</b>\n\n"
        "<b>Имя кластера должно быть уникальным!</b>\n"
        "<i>Пример:</i> <code>cluster1</code> или <code>us_east_1</code>"
    )

    await callback_query.message.answer(
        text=text,
        parse_mode="HTML",
    )

    await state.set_state(AdminServersEditor.waiting_for_cluster_name)


@router.message(
    AdminServersEditor.waiting_for_cluster_name,
    IsAdminFilter()
)
async def handle_cluster_name_input(
        message: types.Message,
        state: FSMContext
):
    if not message.text:
        await message.answer(
            text="❌ Имя кластера не может быть пустым. Попробуйте снова."
        )
        return

    cluster_name = message.text.strip()
    await state.update_data(cluster_name=cluster_name)

    text = (
        f"<b>Введите имя сервера для кластера {cluster_name}:</b>\n\n"
        "Рекомендуется указать локацию сервера в имени.\n\n"
        "<i>Пример:</i> <code>server-frankfurt1</code>, <code>fra1</code>"
    )

    await message.answer(
        text=text,
        parse_mode="HTML",
        reply_markup=build_admin_back_kb("servers_editor"),
    )

    await state.set_state(AdminServersEditor.waiting_for_server_name)


@router.message(
    AdminServersEditor.waiting_for_server_name,
    IsAdminFilter()
)
async def handle_server_name_input(
        message: types.Message,
        state: FSMContext
):
    if not message.text:
        await message.answer(
            text="❌ Имя сервера не может быть пустым. Попробуйте снова."
        )
        return

    server_name = message.text.strip()

    if not await check_unique_server_name(server_name):
        await message.answer(
            text="❌ Сервер с таким именем уже существует. Пожалуйста, выберите другое имя."
        )
        return

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    await state.update_data(server_name=server_name)

    text = (
        f"<b>Введите API URL для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "API URL должен быть в следующем формате:\n\n"
        "<code>https://your_domain:port/panel_path</code>\n\n"
        "URL должен быть без слэша на конце!\n"
    )

    await message.answer(
        text=text,
        parse_mode="HTML",
        reply_markup=build_admin_back_kb("servers_editor"),
    )

    await state.set_state(AdminServersEditor.waiting_for_api_url)


@router.message(
    AdminServersEditor.waiting_for_api_url,
    IsAdminFilter()
)
async def handle_api_url_input(
        message: types.Message,
        state: FSMContext
):
    if not message.text or not message.text.strip().startswith("https://"):
        await message.answer(
            text="❌ API URL должен начинаться с <code>https://</code>. Попробуйте снова.",
            parse_mode="HTML",
        )
        return

    api_url = message.text.strip().rstrip("/")

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    await state.update_data(api_url=api_url)

    text = (
        f"<b>Введите subscription_url для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "Subscription URL должен быть в следующем формате:\n\n"
        "<code>https://your_domain:port_sub/sub_path</code>\n\n"
        "URL должен быть без слэша и имени клиента на конце!\n"
        "Его можно увидеть в панели 3x-ui в информации о клиенте."
    )

    await message.answer(
        text=text,
        parse_mode="HTML",
        reply_markup=build_cancel_kb(),
    )

    await state.set_state(AdminServersEditor.waiting_for_subscription_url)


@router.message(
    AdminServersEditor.waiting_for_subscription_url,
    IsAdminFilter()
)
async def handle_subscription_url_input(
        message: types.Message,
        state: FSMContext
):
    if not message.text or not message.text.strip().startswith("https://"):
        await message.answer(
            text="❌ subscription_url должен начинаться с <code>https://</code>. Попробуйте снова.",
            parse_mode="HTML",
        )
        return

    subscription_url = message.text.strip().rstrip("/")

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    await state.update_data(subscription_url=subscription_url)

    text = (
        f"<b>Введите inbound_id для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "Это номер подключения vless в вашей панели 3x-ui. Обычно это <b>1</b> при чистой настройке по гайду.\n\n"
    )

    await message.answer(
        text=text,
        parse_mode="HTML",
        reply_markup=build_admin_back_kb("servers_editor"),
    )
    await state.set_state(AdminServersEditor.waiting_for_inbound_id)


@router.message(
    AdminServersEditor.waiting_for_inbound_id,
    IsAdminFilter()
)
async def handle_inbound_id_input(
        message: types.Message,
        state: FSMContext
):
    inbound_id = message.text.strip()

    if not inbound_id.isdigit():
        await message.answer(
            text="❌ inbound_id должен быть числовым значением. Попробуйте снова."
        )
        return

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    api_url = user_data.get("api_url")
    subscription_url = user_data.get("subscription_url")

    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        INSERT INTO servers (cluster_name, server_name, api_url, subscription_url, inbound_id) 
        VALUES ($1, $2, $3, $4, $5)
        """,
        cluster_name,
        server_name,
        api_url,
        subscription_url,
        inbound_id,
    )
    await conn.close()

    await message.answer(
        text=f"✅ Кластер {cluster_name} и сервер {server_name} успешно добавлены!",
        reply_markup=build_admin_back_kb("servers_editor"),
    )

    await state.clear()


@router.callback_query(
    AdminServerEditorCallback.filter(F.action == "clusters_manage"),
    IsAdminFilter()
)
async def handle_manage_cluster(
        callback_query: types.CallbackQuery,
        callback_data: AdminServerEditorCallback,
):
    cluster_name = callback_data.data

    servers = await get_servers_from_db()
    cluster_servers = servers.get(cluster_name, [])

    await callback_query.message.answer(
        text=f"🔧 Управление серверами для кластера {cluster_name}",
        reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
    )


@router.callback_query(
    AdminServerEditorCallback.filter(F.action == "servers_availability"),
    IsAdminFilter()
)
async def handle_check_server_availability(
        callback_query: types.CallbackQuery,
        callback_data: AdminServerEditorCallback
):
    cluster_name = callback_data.data

    servers = await get_servers_from_db()
    cluster_servers = servers.get(cluster_name, [])

    if not cluster_servers:
        await callback_query.answer(
            text=f"Кластер '{cluster_name}' не содержит серверов."
        )
        return

    text = (
        f"🖥️ Проверка доступности серверов для кластера {cluster_name}.\n\n"
        "Это может занять до 1 минуты, пожалуйста, подождите..."
    )

    in_progress_message = await callback_query.message.answer(
        text=text
    )

    text = (
        f"🖥️ Проверка доступности серверов для кластера {cluster_name} завершена:\n\n"
    )

    for server in cluster_servers:
        xui = AsyncApi(
            server["api_url"], username=ADMIN_USERNAME, password=ADMIN_PASSWORD
        )

        try:
            await xui.login()

            online_users = len(await xui.client.online())
            text += (
                f"🌍 {server['server_name']}: {online_users} активных пользователей.\n"
            )

        except Exception as e:
            text += f"❌ {server['server_name']}: Не удалось получить информацию. Ошибка: {e}\n"

    await in_progress_message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("servers_editor")
    )

    await callback_query.answer()


@router.callback_query(
    AdminServerEditorCallback.filter(F.action == "servers_manage"),
    IsAdminFilter()
)
async def handle_manage_server(
        callback_query: types.CallbackQuery,
        callback_data: AdminServerEditorCallback
):
    server_name = callback_data.data
    servers = await get_servers_from_db()

    cluster_name, server = next(
        ((c, s) for c, cs in servers.items()
         for s in cs if s["server_name"] == server_name),
        (None, None)
    )

    if server:
        api_url = server["api_url"]
        subscription_url = server["subscription_url"]
        inbound_id = server["inbound_id"]

        text = (
            f"<b>🔧 Информация о сервере {server_name}:</b>\n\n"
            f"<b>📡 API URL:</b> {api_url}\n"
            f"<b>🌐 Subscription URL:</b> {subscription_url}\n"
            f"<b>🔑 Inbound ID:</b> {inbound_id}"
        )

        await callback_query.message.answer(
            text=text,
            parse_mode="HTML",
            reply_markup=build_manage_server_kb(server_name, cluster_name),
        )
    else:
        await callback_query.message.answer("❌ Сервер не найден.")


@router.callback_query(
    AdminServerEditorCallback.filter(F.action == "servers_delete"),
    IsAdminFilter()
)
async def handle_delete_server(
        callback_query: types.CallbackQuery,
        callback_data: AdminServerEditorCallback
):
    server_name = callback_data.data

    await callback_query.message.answer(
        text=f"🗑️ Вы уверены, что хотите удалить сервер {server_name}?",
        reply_markup=build_delete_server_kb(server_name),
    )


@router.callback_query(
    AdminServerEditorCallback.filter(F.action == "servers_delete_confirm"),
    IsAdminFilter()
)
async def handle_confirm_delete_server(
        callback_query: types.CallbackQuery,
        callback_data: AdminServerEditorCallback
):
    server_name = callback_data.data

    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        DELETE FROM servers WHERE server_name = $1
        """,
        server_name,
    )
    await conn.close()

    await callback_query.message.answer(
        text=f"🗑️ Сервер {server_name} успешно удален.",
        reply_markup=build_admin_back_kb("servers_editor")
    )


@router.callback_query(
    AdminServerEditorCallback.filter(F.action == "servers_add"),
    IsAdminFilter()
)
async def handle_add_server(
        callback_query: types.CallbackQuery,
        callback_data: AdminServerEditorCallback,
        state: FSMContext
):
    cluster_name = callback_data.data

    await state.update_data(cluster_name=cluster_name)

    text = (
        f"<b>Введите имя сервера для кластера {cluster_name}:</b>\n\n"
        "Рекомендуется указать локацию сервера в имени.\n\n"
        "<i>Пример:</i> <code>server-asia</code>, <code>server-europe</code>"
    )

    await callback_query.message.answer(
        text=text,
        parse_mode="HTML",
        reply_markup=build_admin_back_kb("servers_editor"),
    )

    await state.set_state(AdminServersEditor.waiting_for_server_name)


@router.callback_query(
    AdminServerEditorCallback.filter(F.action == "clusters_backup"),
    IsAdminFilter()
)
async def handle_backup_cluster(
        callback_query: types.CallbackQuery,
        callback_data: AdminServerEditorCallback,
):
    cluster_name = callback_data.data

    servers = await get_servers_from_db()
    cluster_servers = servers.get(cluster_name, [])

    for server in cluster_servers:
        xui = AsyncApi(
            server["api_url"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )
        await create_backup_and_send_to_admins(xui)

    text = (
        f"<b>Бэкап для кластера {cluster_name} был успешно создан и отправлен администраторам!</b>\n\n"
        f"🔔 <i>Бэкапы отправлены в боты панелей.</i>"
    )

    await callback_query.message.answer(
        text=text,
        parse_mode="HTML",
        reply_markup=build_admin_back_kb("servers_editor"),
    )
    await callback_query.answer()
