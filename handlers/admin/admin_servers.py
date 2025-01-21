import asyncio

import asyncpg
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from py3xui import AsyncApi

from backup import create_backup_and_send_to_admins
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL
from database import check_unique_server_name, get_servers_from_db
from filters.admin import IsAdminFilter
from handlers.keys.key_utils import create_key_on_cluster
from logger import logger

router = Router()


class UserEditorState(StatesGroup):
    waiting_for_cluster_name = State()
    waiting_for_api_url = State()
    waiting_for_inbound_id = State()
    waiting_for_server_name = State()
    waiting_for_subscription_url = State()


@router.callback_query(F.data == "servers_editor", IsAdminFilter())
async def handle_servers_editor(callback_query: types.CallbackQuery):
    servers = await get_servers_from_db()

    builder = InlineKeyboardBuilder()

    for cluster_name, cluster_servers in servers.items():
        builder.row(InlineKeyboardButton(text=f"⚙️ {cluster_name}", callback_data=f"manage_cluster|{cluster_name}"))

    builder.row(InlineKeyboardButton(text="➕ Добавить кластер", callback_data="add_cluster"))
    builder.row(InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin"))

    await callback_query.message.answer(
        "<b>🔧 Управление кластерами</b>\n\n"
        "<i>📌 Здесь вы можете добавить новый кластер.</i>\n\n"
        "<i>🌐 <b>Кластеры</b> — это пространство серверов, в пределах которого создается подписка.</i>\n"
        "💡 Если вы хотите выдавать по 1 серверу, то добавьте всего 1 сервер в кластер.\n\n"
        "<i>⚠️ <b>Важно:</b> Кластеры удаляются автоматически, если удалить все серверы внутри них.</i>\n\n",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "add_cluster", IsAdminFilter())
async def handle_add_cluster(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.answer(
        "🔧 <b>Введите имя нового кластера:</b>\n\n"
        "<b>Имя кластера должно быть уникальным и на английском языке.</b>\n"
        "<i>Пример:</i> <code>cluster2</code> или <code>us_east_1</code>"
    )

    await state.set_state(UserEditorState.waiting_for_cluster_name)


@router.message(UserEditorState.waiting_for_cluster_name, IsAdminFilter())
async def handle_cluster_name_input(message: types.Message, state: FSMContext):
    cluster_name = message.text.strip()

    if cluster_name == "❌ Отменить":
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔧 Управление кластерами", callback_data="servers_editor"))
        await message.answer(
            "Процесс создания кластера отменен. Вы вернулись в меню управления серверами.",
            reply_markup=builder.as_markup(),
        )
        return

    if not cluster_name:
        await message.answer("❌ Имя кластера не может быть пустым. Попробуйте снова.")
        return

    await state.update_data(cluster_name=cluster_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="servers_editor"))

    await message.answer(
        f"<b>Введите имя сервера для кластера {cluster_name}:</b>\n\n"
        "Рекомендуется указать локацию сервера в имени.\n\n"
        "<i>Пример:</i> <code>server-asia</code>, <code>server-europe</code>",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(UserEditorState.waiting_for_server_name)


@router.message(UserEditorState.waiting_for_server_name, IsAdminFilter())
async def handle_server_name_input(message: types.Message, state: FSMContext):
    server_name = message.text.strip()

    if server_name == "❌ Отменить":
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔧 Управление кластерами", callback_data="servers_editor"))
        await message.answer(
            "Процесс создания кластера был отменен. Вы вернулись в меню управления серверами.",
            reply_markup=builder.as_markup(),
        )
        return

    if not server_name:
        await message.answer("❌ Имя сервера не может быть пустым. Попробуйте снова.")
        return

    server_unique = await check_unique_server_name(server_name)
    if not server_unique:
        await message.answer("❌ Сервер с таким именем уже существует. Пожалуйста, выберите другое имя.")
        return

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    await state.update_data(server_name=server_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="servers_editor"))

    await message.answer(
        f"<b>Введите API URL для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "API URL должен быть в следующем формате:\n\n"
        "<code>https://your_domain:port/panel_path</code>\n\n"
        "URL должен быть без слэша на конце!\n",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(UserEditorState.waiting_for_api_url)


@router.message(UserEditorState.waiting_for_api_url, IsAdminFilter())
async def handle_api_url_input(message: types.Message, state: FSMContext):
    api_url = message.text.strip()

    if api_url == "❌ Отменить":
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔧 Управление кластерами", callback_data="servers_editor"))
        await message.answer(
            "Процесс создания кластера был отменен. Вы вернулись в меню управления серверами.",
            reply_markup=builder.as_markup(),
        )
        return

    if not api_url.startswith("https://"):
        await message.answer(
            "❌ API URL должен начинаться с <code>https://</code>. Попробуйте снова.",
        )
        return

    api_url = api_url.rstrip("/")

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    await state.update_data(api_url=api_url)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="servers_editor"))

    await message.answer(
        f"<b>Введите subscription_url для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "Subscription URL должен быть в следующем формате:\n\n"
        "<code>https://your_domain:port_sub/sub_path</code>\n\n"
        "URL должен быть без слэша и имени клиента на конце!\n"
        "Его можно увидеть в панели 3x-ui в информации о клиенте.",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(UserEditorState.waiting_for_subscription_url)


@router.message(UserEditorState.waiting_for_subscription_url, IsAdminFilter())
async def handle_subscription_url_input(message: types.Message, state: FSMContext):
    subscription_url = message.text.strip()

    if subscription_url == "❌ Отменить":
        await state.clear()
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🔧 Управление кластерами", callback_data="servers_editor"))
        await message.answer(
            "Процесс создания кластера был отменен. Вы вернулись в меню управления серверами.",
            reply_markup=builder.as_markup(),
        )
        return

    if not subscription_url.startswith("https://"):
        await message.answer(
            "❌ subscription_url должен начинаться с <code>https://</code>. Попробуйте снова.",
        )
        return

    subscription_url = subscription_url.rstrip("/")

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    await state.update_data(subscription_url=subscription_url)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="servers_editor"))

    await message.answer(
        f"<b>Введите inbound_id для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
        "Это номер подключения vless в вашей панели 3x-ui. Обычно это <b>1</b> при чистой настройке по гайду.\n\n",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(UserEditorState.waiting_for_inbound_id)


@router.message(UserEditorState.waiting_for_inbound_id, IsAdminFilter())
async def handle_inbound_id_input(message: types.Message, state: FSMContext):
    inbound_id = message.text.strip()

    if not inbound_id.isdigit():
        await message.answer("❌ inbound_id должен быть числовым значением. Попробуйте снова.")
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

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад к кластерам", callback_data="servers_editor"))

    await message.answer(
        f"✅ Кластер {cluster_name} и сервер {server_name} успешно добавлены!",
        reply_markup=builder.as_markup(),
    )

    await state.clear()


@router.callback_query(F.data.startswith("manage_cluster|"), IsAdminFilter())
async def handle_manage_cluster(callback_query: types.CallbackQuery, state: FSMContext):
    cluster_name = callback_query.data.split("|")[1]

    servers = await get_servers_from_db()
    cluster_servers = servers.get(cluster_name, [])

    builder = InlineKeyboardBuilder()

    for server in cluster_servers:
        builder.row(
            InlineKeyboardButton(
                text=f"🌍 {server['server_name']}",
                callback_data=f"manage_server|{server['server_name']}",
            )
        )

    builder.row(InlineKeyboardButton(text="➕ Добавить сервер", callback_data=f"add_server|{cluster_name}"))

    builder.row(
        InlineKeyboardButton(
            text="🌐 Доступность серверов",
            callback_data=f"server_availability|{cluster_name}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="💾 Создать бэкап кластера",
            callback_data=f"backup_cluster|{cluster_name}",
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Синхронизировать",
            callback_data=f"sync_cluster|{cluster_name}",
        )
    )

    builder.row(InlineKeyboardButton(text="🔙 Назад в управление кластерами", callback_data="servers_editor"))

    await callback_query.message.answer(
        f"🔧 Управление серверами для кластера {cluster_name}",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("sync_cluster|"), IsAdminFilter())
async def sync_cluster_handler(callback_query: types.CallbackQuery):
    """Обработчик для синхронизации ключей на всех серверах выбранного кластера."""
    cluster_name = callback_query.data.split("|")[1]

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        query_keys = """
            SELECT tg_id, client_id, email, expiry_time
            FROM keys
            WHERE server_id = $1
        """
        keys_to_sync = await conn.fetch(query_keys, cluster_name)

        if not keys_to_sync:
            await callback_query.message.answer(
                f"❌ Нет ключей для синхронизации в кластере {cluster_name}.",
                reply_markup=InlineKeyboardBuilder()
                .row(InlineKeyboardButton(text="🔙 Назад", callback_data="servers_editor"))
                .as_markup(),
            )
            return

        servers = await get_servers_from_db()
        cluster_servers = servers.get(cluster_name, [])

        for key in keys_to_sync:
            for server_info in cluster_servers:
                try:
                    await create_key_on_cluster(
                        cluster_name,
                        key["tg_id"],
                        key["client_id"],
                        key["email"],
                        key["expiry_time"],
                    )
                    await asyncio.sleep(0.6)
                except Exception as e:
                    logger.error(f"Ошибка при добавлении ключа {key['client_id']} в кластер {cluster_name}: {e}")

        await callback_query.message.answer(
            f"✅ Ключи успешно синхронизированы для кластера {cluster_name}.",
            reply_markup=InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text="🔙 Назад", callback_data="servers_editor"))
            .as_markup(),
        )
    except Exception as e:
        logger.error(f"Ошибка синхронизации ключей в кластере {cluster_name}: {e}")
        await callback_query.message.answer(
            f"❌ Произошла ошибка при синхронизации: {e}",
            reply_markup=InlineKeyboardBuilder()
            .row(InlineKeyboardButton(text="🔙 Назад", callback_data="servers_editor"))
            .as_markup(),
        )
    finally:
        await conn.close()


@router.callback_query(F.data.startswith("server_availability|"), IsAdminFilter())
async def handle_check_server_availability(callback_query: types.CallbackQuery):
    cluster_name = callback_query.data.split("|")[1]

    servers = await get_servers_from_db()
    cluster_servers = servers.get(cluster_name, [])

    if not cluster_servers:
        await callback_query.answer(f"Кластер '{cluster_name}' не содержит серверов.")
        return

    in_progress_message = await callback_query.message.answer(
        f"🖥️ Проверка доступности серверов для кластера {cluster_name}.\n\n"
        "Это может занять до 1 минуты, пожалуйста, подождите..."
    )

    availability_message = f"🖥️ Проверка доступности серверов для кластера {cluster_name} завершена:\n\n"

    for server in cluster_servers:
        xui = AsyncApi(server["api_url"], username=ADMIN_USERNAME, password=ADMIN_PASSWORD)

        try:
            await xui.login()

            online_users = len(await xui.client.online())
            availability_message += f"🌍 {server['server_name']}: {online_users} активных пользователей.\n"

        except Exception as e:
            availability_message += f"❌ {server['server_name']}: Не удалось получить информацию. Ошибка: {e}\n"

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"manage_cluster|{cluster_name}"))

    await in_progress_message.edit_text(availability_message, reply_markup=builder.as_markup())

    await callback_query.answer()


@router.callback_query(F.data.startswith("manage_server|"), IsAdminFilter())
async def handle_manage_server(callback_query: types.CallbackQuery, state: FSMContext):
    server_name = callback_query.data.split("|")[1]

    servers = await get_servers_from_db()

    server = None
    cluster_name = None
    for cluster, cluster_servers in servers.items():
        server = next((s for s in cluster_servers if s["server_name"] == server_name), None)
        if server:
            cluster_name = cluster
            break

    if server:
        api_url = server["api_url"]
        subscription_url = server["subscription_url"]
        inbound_id = server["inbound_id"]

        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"delete_server|{server_name}"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data=f"manage_cluster|{cluster_name}"))

        await callback_query.message.answer(
            f"<b>🔧 Информация о сервере {server_name}:</b>\n\n"
            f"<b>📡 API URL:</b> {api_url}\n"
            f"<b>🌐 Subscription URL:</b> {subscription_url}\n"
            f"<b>🔑 Inbound ID:</b> {inbound_id}",
            reply_markup=builder.as_markup(),
        )
    else:
        await callback_query.message.answer("❌ Сервер не найден.")


@router.callback_query(F.data.startswith("delete_server|"), IsAdminFilter())
async def handle_delete_server(callback_query: types.CallbackQuery, state: FSMContext):
    server_name = callback_query.data.split("|")[1]

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да", callback_data=f"confirm_delete_server|{server_name}"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"manage_server|{server_name}"),
    )

    await callback_query.message.answer(
        f"🗑️ Вы уверены, что хотите удалить сервер {server_name}?",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("confirm_delete_server|"), IsAdminFilter())
async def handle_confirm_delete_server(callback_query: types.CallbackQuery, state: FSMContext):
    server_name = callback_query.data.split("|")[1]

    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        DELETE FROM servers WHERE server_name = $1
        """,
        server_name,
    )
    await conn.close()

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад в управление кластерами", callback_data="servers_editor"))

    await callback_query.message.answer(f"🗑️ Сервер {server_name} успешно удален.", reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("add_server|"), IsAdminFilter())
async def handle_add_server(callback_query: types.CallbackQuery, state: FSMContext):
    cluster_name = callback_query.data.split("|")[1]

    await state.update_data(cluster_name=cluster_name)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="servers_editor"))

    await callback_query.message.answer(
        f"<b>Введите имя сервера для кластера {cluster_name}:</b>\n\n"
        "Рекомендуется указать локацию сервера в имени.\n\n"
        "<i>Пример:</i> <code>server-asia</code>, <code>server-europe</code>",
        reply_markup=builder.as_markup(),
    )

    await state.set_state(UserEditorState.waiting_for_server_name)


@router.callback_query(F.data.startswith("backup_cluster|"), IsAdminFilter())
async def handle_backup_cluster(callback_query: types.CallbackQuery):
    cluster_name = callback_query.data.split("|")[1]

    servers = await get_servers_from_db()
    cluster_servers = servers.get(cluster_name, [])

    for server in cluster_servers:
        xui = AsyncApi(
            server["api_url"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )
        await create_backup_and_send_to_admins(xui)

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="servers_editor"))

    await callback_query.message.answer(
        f"<b>Бэкап для кластера {cluster_name} был успешно создан и отправлен администраторам!</b>\n\n"
        f"🔔 <i>Бэкапы отправлены в боты панелей.</i>",
        reply_markup=builder.as_markup(),
    )
    await callback_query.answer()
