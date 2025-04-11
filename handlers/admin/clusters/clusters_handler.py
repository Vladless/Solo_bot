import asyncio
import time

from typing import Any

import asyncpg

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from py3xui import AsyncApi
from datetime import datetime, timedelta

from backup import create_backup_and_send_to_admins
from config import ADMIN_PASSWORD, ADMIN_USERNAME, DATABASE_URL, TOTAL_GB, USE_COUNTRY_SELECTION, REMNAWAVE_PASSWORD, REMNAWAVE_LOGIN
from database import check_unique_server_name, get_servers, update_key_expiry
from filters.admin import IsAdminFilter
from handlers.keys.key_utils import create_client_on_server, create_key_on_cluster, renew_key_in_cluster, delete_key_from_cluster
from logger import logger

from ..panel.keyboard import AdminPanelCallback, build_admin_back_kb
from .keyboard import (
    AdminClusterCallback,
    AdminServerCallback,
    build_cluster_management_kb,
    build_clusters_editor_kb,
    build_manage_cluster_kb,
    build_sync_cluster_kb,
    build_panel_type_kb
)
from panels.remnawave import RemnawaveAPI


router = Router()


class AdminClusterStates(StatesGroup):
    waiting_for_cluster_name = State()
    waiting_for_api_url = State()
    waiting_for_inbound_id = State()
    waiting_for_server_name = State()
    waiting_for_subscription_url = State()
    waiting_for_days_input = State()
    waiting_for_new_cluster_name = State()
    waiting_for_new_server_name = State()
    waiting_for_server_transfer = State()
    waiting_for_cluster_transfer = State()


@router.callback_query(
    AdminPanelCallback.filter(F.action == "clusters"),
    IsAdminFilter(),
)
async def handle_servers(callback_query: CallbackQuery):
    servers = await get_servers()

    text = (
        "<b>🔧 Управление кластерами</b>\n\n"
        "<i>📌 Здесь вы можете добавить новый кластер.</i>\n\n"
        "<i>🌐 <b>Кластеры</b> — это пространство серверов, в пределах которого создается подписка.</i>\n"
        "💡 Если вы хотите выдавать по 1 серверу, то добавьте всего 1 сервер в кластер.\n\n"
        "<i>⚠️ <b>Важно:</b> Кластеры удаляются автоматически, если удалить все серверы внутри них.</i>\n\n"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_clusters_editor_kb(servers),
    )


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
            text="❌ Имя кластера не может быть пустым! Попробуйте снова.", reply_markup=build_admin_back_kb("clusters")
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
            text="❌ Имя сервера не может быть пустым. Попробуйте снова.", reply_markup=build_admin_back_kb("clusters")
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

    if not await check_unique_server_name(server_name, session, cluster_name):
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
        text=f"<b>Введите inbound_id для сервера {server_name} в кластере {cluster_name}:</b>\n\n"
             f"Для Remnawave это UUID Инбаунда, для 3x-ui — просто ID (например, <code>1</code>).",
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


@router.callback_query(AdminClusterCallback.filter(F.action.in_(["panel_3xui", "panel_remnawave"])), IsAdminFilter())
async def handle_panel_type_selection(callback_query: CallbackQuery, callback_data: AdminClusterCallback, state: FSMContext):
    panel_type = "3x-ui" if callback_data.action == "panel_3xui" else "remnawave"

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")
    server_name = user_data.get("server_name")
    api_url = user_data.get("api_url")
    subscription_url = user_data.get("subscription_url")
    inbound_id = user_data.get("inbound_id")

    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        """
        INSERT INTO servers (cluster_name, server_name, api_url, subscription_url, inbound_id, panel_type)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        cluster_name,
        server_name,
        api_url,
        subscription_url,
        inbound_id,
        panel_type,
    )
    await conn.close()

    await callback_query.message.edit_text(
        text=f"✅ Сервер <b>{server_name}</b> с панелью <b>{panel_type}</b> успешно добавлен в кластер <b>{cluster_name}</b>!",
        reply_markup=build_admin_back_kb("clusters"),
    )
    await state.clear()


@router.callback_query(AdminClusterCallback.filter(F.action == "manage"), IsAdminFilter())
async def handle_clusters_manage(
    callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any
):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    await callback_query.message.edit_text(
        text=f"<b>🔧 Управление кластером {cluster_name}</b>",
        reply_markup=build_manage_cluster_kb(cluster_servers, cluster_name),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "availability"), IsAdminFilter())
async def handle_cluster_availability(
    callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any
):
    cluster_name = callback_data.data
    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    if not cluster_servers:
        await callback_query.message.edit_text(text=f"Кластер '{cluster_name}' не содержит серверов.")
        return

    await callback_query.message.edit_text(
        text=(
            f"🖥️ Проверка доступности серверов для кластера {cluster_name}.\n\n"
            "Это может занять до 1 минуты, пожалуйста, подождите..."
        )
    )

    total_online_users = 0
    result_text = f"<b>🖥️ Проверка доступности серверов</b>\n\n⚙️ Кластер: <b>{cluster_name}</b>\n\n"

    now = datetime.utcnow()
    start_time = now - timedelta(minutes=5)
    start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    for server in cluster_servers:
        server_name = server["server_name"]
        panel_type = server.get("panel_type", "3x-ui").lower()
        prefix = "[3x]" if panel_type == "3x-ui" else "[Re]"

        try:
            if panel_type == "3x-ui":
                xui = AsyncApi(server["api_url"], username=ADMIN_USERNAME, password=ADMIN_PASSWORD, logger=None)
                await xui.login()
                inbound_id = int(server["inbound_id"])
                online_clients = await xui.client.online()
                online_inbound_users = 0

                for client_email in online_clients:
                    client = await xui.client.get_by_email(client_email)
                    if client and client.inbound_id == inbound_id:
                        online_inbound_users += 1

                total_online_users += online_inbound_users
                result_text += f"🌍 <b>{prefix} {server_name}</b> - {online_inbound_users} онлайн\n"

            elif panel_type == "remnawave":
                remna = RemnawaveAPI(server["api_url"])
                if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    raise Exception("Не удалось авторизоваться")

                node_uuid = server.get("inbound_id")
                if not node_uuid:
                    raise Exception("Не указан UUID ноды (inbound_id)")

                data = await remna.get_node_users_usage(node_uuid, start=start_iso, end=end_iso)
                online_remna_users = len(data) if data else 0
                total_online_users += online_remna_users
                result_text += f"🌍 <b>{prefix} {server_name}</b> - {online_remna_users} онлайн\n"

        except Exception as e:
            error_text = str(e) or "Сервер недоступен"
            result_text += f"❌ <b>{prefix} {server_name}</b> - ошибка: {error_text}\n"

    result_text += f"\n👥 Всего пользователей онлайн: {total_online_users}"
    await callback_query.message.edit_text(text=result_text, reply_markup=build_admin_back_kb("clusters"))


@router.callback_query(AdminClusterCallback.filter(F.action == "backup"), IsAdminFilter())
async def handle_clusters_backup(
    callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any
):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    for server in cluster_servers:
        xui = AsyncApi(
            server["api_url"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
            logger=logger,
        )
        await create_backup_and_send_to_admins(xui)

    text = (
        f"<b>Бэкап для кластера {cluster_name} был успешно создан и отправлен администраторам!</b>\n\n"
        f"🔔 <i>Бэкапы отправлены в боты панелей.</i>"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync"), IsAdminFilter())
async def handle_sync(callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    await callback_query.message.edit_text(
        text=f"<b>🔄 Синхронизация кластера {cluster_name}</b>",
        reply_markup=build_sync_cluster_kb(cluster_servers, cluster_name),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync-server"), IsAdminFilter())
async def handle_sync_server(callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any):
    server_name = callback_data.data

    try:
        query_keys = """
                SELECT s.*, k.tg_id, k.client_id, k.email, k.expiry_time
                FROM servers s
                JOIN keys k ON s.cluster_name = k.server_id
                WHERE s.server_name = $1;
            """
        keys_to_sync = await session.fetch(query_keys, server_name)

        if not keys_to_sync:
            await callback_query.message.edit_text(
                text=f"❌ Нет ключей для синхронизации в сервере {server_name}.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        text = f"<b>🔄 Синхронизация сервера {server_name}</b>\n\n🔑 Количество ключей: <b>{len(keys_to_sync)}</b>"

        await callback_query.message.edit_text(
            text=text,
        )

        semaphore = asyncio.Semaphore(2)
        for key in keys_to_sync:
            try:
                await create_client_on_server(
                    {
                        "api_url": key["api_url"],
                        "inbound_id": key["inbound_id"],
                        "server_name": key["server_name"],
                    },
                    key["tg_id"],
                    key["client_id"],
                    key["email"],
                    key["expiry_time"],
                    semaphore,
                )
                await asyncio.sleep(0.6)
            except Exception as e:
                logger.error(f"Ошибка при добавлении ключа {key['client_id']} в сервер {server_name}: {e}")

        await callback_query.message.edit_text(
            text=f"✅ Ключи успешно синхронизированы для сервера {server_name}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        logger.error(f"Ошибка синхронизации ключей для сервера {server_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при синхронизации: {e}", reply_markup=build_admin_back_kb("clusters")
        )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync-cluster"), IsAdminFilter())
async def handle_sync_cluster(callback_query: types.CallbackQuery, callback_data: AdminClusterCallback, session: Any):
    cluster_name = callback_data.data

    try:
        query_keys = """
            SELECT tg_id, client_id, email, expiry_time
            FROM keys
            WHERE server_id = $1
        """
        keys_to_sync = await session.fetch(query_keys, cluster_name)

        if not keys_to_sync:
            await callback_query.message.edit_text(
                text=f"❌ Нет ключей для синхронизации в кластере {cluster_name}.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        await callback_query.message.edit_text(
            text=f"<b>🔄 Синхронизация кластера {cluster_name}</b>\n\n🔑 Количество ключей: <b>{len(keys_to_sync)}</b>"
        )
        for key in keys_to_sync:
            try:
                await delete_key_from_cluster(cluster_name, key["email"], key["client_id"])

                await session.execute(
                    "DELETE FROM keys WHERE tg_id = $1 AND client_id = $2",
                    key["tg_id"],
                    key["client_id"]
                )

                result = await create_key_on_cluster(
                    cluster_name,
                    key["tg_id"],
                    key["client_id"],
                    key["email"],
                    key["expiry_time"],
                    session=session,
                )

                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Ошибка при синхронизации ключа {key['client_id']} в {cluster_name}: {e}")


        await callback_query.message.edit_text(
            text=f"✅ Ключи успешно синхронизированы для кластера {cluster_name}",
            reply_markup=build_admin_back_kb("clusters"),
        )

    except Exception as e:
        logger.error(f"Ошибка синхронизации ключей в кластере {cluster_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при синхронизации: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )


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


@router.callback_query(AdminClusterCallback.filter(F.action == "manage_cluster"), IsAdminFilter())
async def handle_manage_cluster_menu(callback_query: CallbackQuery, callback_data: AdminClusterCallback):
    cluster_name = callback_data.data

    await callback_query.message.edit_text(
        text=f"<b>🛠 Управление кластером {cluster_name}</b>\nВыберите действие:",
        reply_markup=build_cluster_management_kb(cluster_name),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "add_time"), IsAdminFilter())
async def handle_add_time(callback_query: CallbackQuery, callback_data: AdminClusterCallback, state: FSMContext):
    cluster_name = callback_data.data
    await state.set_state(AdminClusterStates.waiting_for_days_input)
    await state.update_data(cluster_name=cluster_name)

    await callback_query.message.edit_text(
        f"⏳ Введите количество дней, на которое хотите продлить все подписки в кластере <b>{cluster_name}</b>:",
        reply_markup=build_admin_back_kb("clusters"),
    )


@router.message(AdminClusterStates.waiting_for_days_input, IsAdminFilter())
async def handle_days_input(message: Message, state: FSMContext, session: Any):
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError

        user_data = await state.get_data()
        cluster_name = user_data.get("cluster_name")

        now = int(time.time() * 1000)
        add_ms = days * 86400 * 1000

        keys = await session.fetch(
            "SELECT tg_id, client_id, email, expiry_time FROM keys WHERE server_id = $1",
            cluster_name,
        )

        if not keys:
            await message.answer("❌ Нет подписок в этом кластере.")
            await state.clear()
            return

        for key in keys:
            new_expiry = (key["expiry_time"] or now) + add_ms
            await renew_key_in_cluster(
                cluster_name,
                email=key["email"],
                client_id=key["client_id"],
                new_expiry_time=new_expiry,
                total_gb=TOTAL_GB,
            )
            await update_key_expiry(key["client_id"], new_expiry, session)

        await message.answer(
            f"✅ Время подписки продлено на <b>{days} дней</b> всем пользователям в кластере <b>{cluster_name}</b>."
        )
    except ValueError:
        await message.answer("❌ Введите корректное число дней.")
        return
    except Exception as e:
        logger.error(f"Ошибка при добавлении дней: {e}")
        await message.answer("❌ Произошла ошибка при продлении времени.")
    finally:
        await state.clear()


@router.callback_query(AdminClusterCallback.filter(F.action == "rename"), IsAdminFilter())
async def handle_rename_cluster(callback_query: CallbackQuery, callback_data: AdminClusterCallback, state: FSMContext):
    cluster_name = callback_data.data
    await state.update_data(old_cluster_name=cluster_name)

    text = (
        f"✏️ <b>Введите новое имя для кластера '{cluster_name}':</b>\n\n"
        "▸ Имя должно быть уникальным.\n"
        "▸ Имя не должно превышать 12 символов.\n\n"
        "📌 <i>Пример:</i> <code>new_cluster</code>"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )
    await state.set_state(AdminClusterStates.waiting_for_new_cluster_name)


@router.message(AdminClusterStates.waiting_for_new_cluster_name, IsAdminFilter())
async def handle_new_cluster_name_input(message: Message, state: FSMContext, session: Any):
    if not message.text:
        await message.answer(
            text="❌ Имя кластера не может быть пустым! Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    new_cluster_name = message.text.strip()
    if len(new_cluster_name) > 12:
        await message.answer(
            text="❌ Имя кластера не должно превышать 12 символов! Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    user_data = await state.get_data()
    old_cluster_name = user_data.get("old_cluster_name")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing_cluster = await conn.fetchval(
            "SELECT cluster_name FROM servers WHERE cluster_name = $1 LIMIT 1",
            new_cluster_name
        )
        if existing_cluster:
            await message.answer(
                text=f"❌ Кластер с именем '{new_cluster_name}' уже существует. Введите другое имя.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        keys_count = await conn.fetchval(
            "SELECT COUNT(*) FROM keys WHERE server_id = $1",
            old_cluster_name
        )

        async with conn.transaction():
            await conn.execute(
                "UPDATE servers SET cluster_name = $1 WHERE cluster_name = $2",
                new_cluster_name,
                old_cluster_name
            )

            if keys_count > 0:
                await conn.execute(
                    "UPDATE keys SET server_id = $1 WHERE server_id = $2",
                    new_cluster_name,
                    old_cluster_name
                )

        await message.answer(
            text=f"✅ Название кластера успешно изменено с '{old_cluster_name}' на '{new_cluster_name}'!",
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        logger.error(f"Ошибка при смене имени кластера {old_cluster_name} на {new_cluster_name}: {e}")
        await message.answer(
            text=f"❌ Произошла ошибка при смене имени кластера: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    finally:
        await conn.close()
        await state.clear()


@router.callback_query(AdminServerCallback.filter(F.action == "rename"), IsAdminFilter())
async def handle_rename_server(callback_query: CallbackQuery, callback_data: AdminServerCallback, state: FSMContext):
    old_server_name = callback_data.data

    servers = await get_servers()
    cluster_name = None
    for c_name, server_list in servers.items():
        for server in server_list:
            if server["server_name"] == old_server_name:
                cluster_name = c_name
                break
        if cluster_name:
            break

    if not cluster_name:
        await callback_query.message.edit_text(
            text=f"❌ Не удалось найти кластер для сервера '{old_server_name}'.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    await state.update_data(old_server_name=old_server_name, cluster_name=cluster_name)

    text = (
        f"✏️ <b>Введите новое имя для сервера '{old_server_name}' в кластере '{cluster_name}':</b>\n\n"
        "▸ Имя должно быть уникальным в пределах кластера.\n"
        "▸ Имя не должно превышать 12 символов.\n\n"
        "📌 <i>Пример:</i> <code>new_server</code>"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )
    await state.set_state(AdminClusterStates.waiting_for_new_server_name)


@router.message(AdminClusterStates.waiting_for_new_server_name, IsAdminFilter())
async def handle_new_server_name_input(message: Message, state: FSMContext, session: Any):
    if not message.text:
        await message.answer(
            text="❌ Имя сервера не может быть пустым! Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    new_server_name = message.text.strip()
    if len(new_server_name) > 12:
        await message.answer(
            text="❌ Имя сервера не должно превышать 12 символов! Попробуйте снова.",
            reply_markup=build_admin_back_kb("clusters"),
        )
        return

    user_data = await state.get_data()
    old_server_name = user_data.get("old_server_name")
    cluster_name = user_data.get("cluster_name")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        existing_server = await conn.fetchval(
            "SELECT server_name FROM servers WHERE cluster_name = $1 AND server_name = $2 LIMIT 1",
            cluster_name,
            new_server_name
        )
        if existing_server:
            await message.answer(
                text=f"❌ Сервер с именем '{new_server_name}' уже существует в кластере '{cluster_name}'. Введите другое имя.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        keys_count = await conn.fetchval(
            "SELECT COUNT(*) FROM keys WHERE server_id = $1",
            old_server_name
        )

        async with conn.transaction():
            await conn.execute(
                "UPDATE servers SET server_name = $1 WHERE cluster_name = $2 AND server_name = $3",
                new_server_name,
                cluster_name,
                old_server_name
            )

            if keys_count > 0:
                await conn.execute(
                    "UPDATE keys SET server_id = $1 WHERE server_id = $2",
                    new_server_name,
                    old_server_name
                )

        final_text = f"✅ Название сервера успешно изменено с '{old_server_name}' на '{new_server_name}' в кластере '{cluster_name}'!"

        await message.answer(
            text=final_text,
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        logger.error(f"Ошибка при смене имени сервера {old_server_name} на {new_server_name}: {e}")
        await message.answer(
            text=f"❌ Произошла ошибка при смене имени сервера: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    finally:
        await conn.close()
        await state.clear()


@router.callback_query(F.data.startswith("transfer_to_server|"))
async def handle_server_transfer(callback_query: CallbackQuery, state: FSMContext):
    data = callback_query.data.split("|")
    new_server_name = data[1]
    old_server_name = data[2]

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute(
                "UPDATE keys SET server_id = $1 WHERE server_id = $2",
                new_server_name,
                old_server_name
            )

            await conn.execute(
                "DELETE FROM servers WHERE cluster_name = $1 AND server_name = $2",
                cluster_name,
                old_server_name
            )

        base_text = f"✅ Ключи успешно перенесены на сервер '{new_server_name}', сервер '{old_server_name}' удален!"
        sync_reminder = "\n\n⚠️ Не забудьте сделать \"Синхронизацию\"."
        final_text = base_text + (sync_reminder if USE_COUNTRY_SELECTION else "")

        await callback_query.message.edit_text(
            text=final_text,
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        logger.error(f"Ошибка при переносе ключей на сервер {new_server_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при переносе ключей: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    finally:
        await conn.close()
        await state.clear()


@router.callback_query(F.data.startswith("transfer_to_cluster|"))
async def handle_cluster_transfer(callback_query: CallbackQuery, state: FSMContext):
    data = callback_query.data.split("|")
    new_cluster_name = data[1]
    old_cluster_name = data[2]
    old_server_name = data[3]

    user_data = await state.get_data()
    cluster_name = user_data.get("cluster_name")

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        async with conn.transaction():
            await conn.execute(
                "UPDATE keys SET server_id = $1 WHERE server_id = $2",
                new_cluster_name,
                old_server_name
            )
            await conn.execute(
                "UPDATE keys SET server_id = $1 WHERE server_id = $2",
                new_cluster_name,
                old_cluster_name
            )

            await conn.execute(
                "DELETE FROM servers WHERE cluster_name = $1 AND server_name = $2",
                cluster_name,
                old_server_name
            )

        await callback_query.message.edit_text(
            text=f"✅ Ключи успешно перенесены в кластер '{new_cluster_name}', сервер '{old_server_name}' и кластер '{old_cluster_name}' удалены!\n\n⚠️ Не забудьте сделать \"Синхронизацию\".",
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        logger.error(f"Ошибка при переносе ключей в кластер {new_cluster_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при переносе ключей: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    finally:
        await conn.close()
        await state.clear()
