import asyncio

from datetime import datetime, timezone
from typing import Any

from aiogram import F, types
from aiogram.types import CallbackQuery
from py3xui import AsyncApi
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    USE_COUNTRY_SELECTION,
)
from database import get_servers
from database.models import Key, Server, Tariff
from filters.admin import IsAdminFilter
from handlers.keys.operations import (
    create_client_on_server,
    create_key_on_cluster,
    delete_key_from_cluster,
)
from handlers.keys.operations.aggregated_links import make_aggregated_link
from handlers.utils import ALLOWED_GROUP_CODES
from logger import logger
from panels.remnawave import RemnawaveAPI
from utils.backup import create_backup_and_send_to_admins

from ..panel.keyboard import build_admin_back_kb
from .base import router
from .keyboard import AdminClusterCallback, build_availability_kb, build_sync_cluster_kb


@router.callback_query(AdminClusterCallback.filter(F.action == "availability"), IsAdminFilter())
async def handle_cluster_availability(
    callback_query: types.CallbackQuery,
    callback_data: AdminClusterCallback,
    session: Any,
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

    for server in cluster_servers:
        server_name = server["server_name"]
        panel_type = server.get("panel_type", "3x-ui").lower()
        prefix = "[3x]" if panel_type == "3x-ui" else "[Re]"

        try:
            if panel_type == "3x-ui":
                xui = AsyncApi(
                    server["api_url"],
                    username=ADMIN_USERNAME,
                    password=ADMIN_PASSWORD,
                    logger=None,
                )
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
                server_inbound_id = server.get("inbound_id")
                if not server_inbound_id:
                    raise Exception("Не указан inbound_id сервера")

                remna = RemnawaveAPI(server["api_url"])
                nodes_data = await remna.get_all_nodes_with_online(
                    username=REMNAWAVE_LOGIN,
                    password=REMNAWAVE_PASSWORD,
                    inbound_id=server_inbound_id,
                )

                if nodes_data.get("error"):
                    raise Exception(nodes_data["error"])

                online_remna_users = nodes_data["total_online"]
                total_online_users += online_remna_users

                nodes_info = nodes_data["nodes"]
                result_text += f"🌍 <b>{prefix} {server_name}</b> - {online_remna_users} онлайн\n"
                seen = set()
                for node_info in nodes_info:
                    node_name = node_info.get("name", "Unknown")
                    if node_name in seen:
                        continue
                    seen.add(node_name)

                    country_code = node_info.get("country_code", "Unknown")
                    online_users = node_info.get("online_users", 0)

                    flag = (
                        "".join(chr(ord(c) + 127397) for c in country_code.upper())
                        if country_code != "Unknown" and len(country_code) == 2
                        else country_code
                    )
                    result_text += f"  ↳ {flag} ({node_name}): {online_users} онлайн\n"

        except Exception as e:
            error_text = str(e) or "Сервер недоступен"
            result_text += f"❌ <b>{prefix} {server_name}</b> - ошибка: {error_text}\n"

    result_text += f"\n👥 Всего пользователей онлайн: {total_online_users}"
    await callback_query.message.edit_text(
        text=result_text,
        reply_markup=build_availability_kb(cluster_name),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "backup"), IsAdminFilter())
async def handle_clusters_backup(
    callback_query: types.CallbackQuery,
    callback_data: AdminClusterCallback,
    session: Any,
):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    for server in cluster_servers:
        if server.get("panel_type") == "remnawave":
            continue

        xui = AsyncApi(
            server["api_url"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
            logger=logger,
        )
        await create_backup_and_send_to_admins(xui)

    text = (
        f"<b>Бэкап для кластера {cluster_name} был успешно создан и отправлен администраторам!</b>\n\n"
        f"🔔 <i>Бэкапы отправлены в боты панелей (3x-ui).</i>"
    )

    await callback_query.message.edit_text(
        text=text,
        reply_markup=build_admin_back_kb("clusters"),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync"), IsAdminFilter())
async def handle_sync(
    callback_query: types.CallbackQuery,
    callback_data: AdminClusterCallback,
    session: Any,
):
    cluster_name = callback_data.data

    servers = await get_servers(session)
    cluster_servers = servers.get(cluster_name, [])

    await callback_query.message.edit_text(
        text=f"<b>🔄 Синхронизация кластера {cluster_name}</b>",
        reply_markup=build_sync_cluster_kb(cluster_servers, cluster_name),
    )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync-server"), IsAdminFilter())
async def handle_sync_server(
    callback_query: types.CallbackQuery,
    callback_data: AdminClusterCallback,
    session: AsyncSession,
):
    server_name = callback_data.data

    try:
        server_result = await session.execute(
            select(Server.cluster_name).where(Server.server_name == server_name).limit(1)
        )
        cluster_name = server_result.scalar()

        if not cluster_name:
            await callback_query.message.edit_text(
                text=f"❌ Сервер {server_name} не найден.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        if USE_COUNTRY_SELECTION:
            stmt = (
                select(
                    Server.api_url,
                    Server.inbound_id,
                    Server.server_name,
                    Server.panel_type,
                    Key.tg_id,
                    Key.client_id,
                    Key.email,
                    Key.expiry_time,
                    Key.tariff_id,
                    Key.remnawave_link,
                )
                .join(Key, Server.server_name == Key.server_id)
                .where(Server.server_name == server_name)
            )
        else:
            stmt = (
                select(
                    Server.api_url,
                    Server.inbound_id,
                    Server.server_name,
                    Server.panel_type,
                    Key.tg_id,
                    Key.client_id,
                    Key.email,
                    Key.expiry_time,
                    Key.tariff_id,
                    Key.remnawave_link,
                )
                .join(Key, Server.cluster_name == Key.server_id)
                .where(Server.server_name == server_name)
            )

        result = await session.execute(stmt)
        keys_to_sync = result.mappings().all()

        if not keys_to_sync:
            await callback_query.message.edit_text(
                text=f"❌ Нет ключей для синхронизации в сервере {server_name}.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return

        await callback_query.message.edit_text(
            text=f"<b>🔄 Синхронизация сервера {server_name}</b>\n\n🔑 Количество ключей: <b>{len(keys_to_sync)}</b>"
        )

        semaphore = asyncio.Semaphore(2)
        for key in keys_to_sync:
            try:
                if key["panel_type"] == "remnawave":
                    tariff = None
                    if key["tariff_id"]:
                        tariff = await session.get(Tariff, key["tariff_id"])
                        if tariff:
                            servers = await get_servers(session)
                            server_info = None
                            for cluster_servers in servers.values():
                                for s in cluster_servers:
                                    if s.get("server_name") == server_name:
                                        server_info = s
                                        break
                                if server_info:
                                    break

                            if server_info:
                                if tariff.subgroup_title and tariff.subgroup_title not in server_info.get(
                                    "tariff_subgroups", []
                                ):
                                    continue

                                if tariff.group_code and tariff.group_code.lower() in ALLOWED_GROUP_CODES:
                                    if tariff.group_code.lower() not in server_info.get("special_groups", []):
                                        continue

                    expire_iso = (
                        datetime.utcfromtimestamp(key["expiry_time"] / 1000).replace(tzinfo=timezone.utc).isoformat()
                    )

                    remna = RemnawaveAPI(key["api_url"])
                    if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                        logger.error(f"Не удалось авторизоваться в Remnawave для сервера {server_name}")
                        continue

                    traffic_limit_bytes = 0
                    hwid_limit = 0
                    if tariff:
                        if tariff.traffic_limit is not None:
                            traffic_limit_bytes = int(tariff.traffic_limit * 1024**3)
                        hwid_limit = tariff.device_limit

                    success = await remna.update_user(
                        uuid=key["client_id"],
                        expire_at=expire_iso,
                        telegram_id=key["tg_id"],
                        email=f"{key['email']}@fake.local",
                        active_user_inbounds=[key["inbound_id"]],
                        traffic_limit_bytes=traffic_limit_bytes,
                        hwid_device_limit=hwid_limit,
                    )

                    if success:
                        try:
                            sub = await remna.get_subscription_by_username(key["email"])
                            if sub:
                                new_remnawave_link = sub.get("subscriptionUrl")

                                if new_remnawave_link:
                                    server_result = await session.execute(
                                        select(Server.cluster_name).where(Server.server_name == server_name)
                                    )
                                    cluster_name = server_result.scalar()

                                    servers = await get_servers(session)
                                    cluster_servers = servers.get(cluster_name, [])

                                    key_value = await make_aggregated_link(
                                        session=session,
                                        cluster_all=cluster_servers,
                                        cluster_id=cluster_name,
                                        email=key["email"],
                                        client_id=key["client_id"],
                                        tg_id=key["tg_id"],
                                        remna_link_override=None,
                                        plan=key["tariff_id"],
                                    )

                                    await session.execute(
                                        update(Key)
                                        .where(Key.tg_id == key["tg_id"], Key.client_id == key["client_id"])
                                        .values(remnawave_link=new_remnawave_link, key=key_value)
                                    )
                                    await session.commit()
                                    logger.info(f"[Sync] Обновлена ссылка для {key['email']}: {new_remnawave_link}")
                        except Exception as e:
                            logger.warning(f"[Sync] Не удалось получить ссылку для {key['email']}: {e}")

                    if not success:
                        logger.warning("[Sync] ошибка обновления, пробуем пересоздать")

                        await delete_key_from_cluster(server_name, key["email"], key["client_id"], session)

                        await create_key_on_cluster(
                            cluster_id=server_name,
                            tg_id=key["tg_id"],
                            client_id=key["client_id"],
                            email=key["email"],
                            expiry_timestamp=key["expiry_time"],
                            plan=key["tariff_id"],
                            session=session,
                            remnawave_link=key["remnawave_link"],
                        )
                else:
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
                        plan=key["tariff_id"],
                        session=session,
                    )
                await asyncio.sleep(0.6)
            except Exception as e:
                logger.error(f"Ошибка при синхронизации ключа {key['client_id']} в сервер {server_name}: {e}")

        await callback_query.message.edit_text(
            text=f"✅ Ключи успешно синхронизированы для сервера {server_name}",
            reply_markup=build_admin_back_kb("clusters"),
        )
    except Exception as e:
        logger.error(f"Ошибка синхронизации ключей для сервера {server_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при синхронизации: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )


@router.callback_query(AdminClusterCallback.filter(F.action == "sync-cluster"), IsAdminFilter())
async def handle_sync_cluster(
    callback_query: CallbackQuery,
    callback_data: AdminClusterCallback,
    session: AsyncSession,
):
    cluster_name = callback_data.data

    try:
        servers = await get_servers(session)
        cluster_servers = servers.get(cluster_name, [])

        if USE_COUNTRY_SELECTION:
            server_names = [s.get("server_name") for s in cluster_servers if s.get("server_name")]
            if not server_names:
                await callback_query.message.edit_text(
                    text=f"❌ В кластере {cluster_name} нет серверов.",
                    reply_markup=build_admin_back_kb("clusters"),
                )
                return
            result = await session.execute(
                select(
                    Key.tg_id,
                    Key.client_id,
                    Key.email,
                    Key.expiry_time,
                    Key.remnawave_link,
                    Key.tariff_id,
                    Key.server_id,
                ).where(Key.server_id.in_(server_names), Key.is_frozen.is_(False))
            )
        else:
            result = await session.execute(
                select(
                    Key.tg_id,
                    Key.client_id,
                    Key.email,
                    Key.expiry_time,
                    Key.remnawave_link,
                    Key.tariff_id,
                    Key.server_id,
                ).where(Key.server_id == cluster_name, Key.is_frozen.is_(False))
            )

        keys_to_sync = result.mappings().all()

        if not keys_to_sync:
            await callback_query.message.edit_text(
                text=f"❌ Нет ключей для синхронизации в кластере {cluster_name}.",
                reply_markup=build_admin_back_kb("clusters"),
            )
            return
        only_remnawave = all(s.get("panel_type") == "remnawave" for s in cluster_servers)

        await callback_query.message.edit_text(
            text=f"<b>🔄 Синхронизация кластера {cluster_name}</b>\n\n🔑 Количество ключей: <b>{len(keys_to_sync)}</b>"
        )

        for key in keys_to_sync:
            try:
                if only_remnawave:
                    expire_iso = (
                        datetime.utcfromtimestamp(key["expiry_time"] / 1000).replace(tzinfo=timezone.utc).isoformat()
                    )

                    traffic_limit_bytes = 0
                    hwid_limit = 0
                    subgroup_title = None
                    tariff = None
                    if key["tariff_id"]:
                        tariff = await session.get(Tariff, key["tariff_id"])
                        if tariff:
                            if tariff.traffic_limit is not None:
                                traffic_limit_bytes = int(tariff.traffic_limit * 1024**3)
                            else:
                                traffic_limit_bytes = 0
                            hwid_limit = tariff.device_limit
                            subgroup_title = tariff.subgroup_title
                        else:
                            logger.warning(
                                f"[Sync] Ключ {key['client_id']} с несуществующим тарифом ID={key['tariff_id']} — "
                                f"обновим без лимитов"
                            )

                    if USE_COUNTRY_SELECTION:
                        user_server = None
                        for s in cluster_servers:
                            if s.get("server_name") == key["server_id"]:
                                user_server = s
                                break

                        if not user_server:
                            logger.warning(
                                f"[Sync] Сервер {key['server_id']} не найден в кластере {cluster_name}, пропускаем ключ"
                            )
                            continue

                        remna = RemnawaveAPI(user_server["api_url"])
                        inbound_ids = [user_server["inbound_id"]] if user_server.get("inbound_id") else []
                    else:
                        remna = RemnawaveAPI(cluster_servers[0]["api_url"])

                        filtered_servers = cluster_servers
                        if subgroup_title:
                            filtered_servers = [
                                s for s in cluster_servers if subgroup_title in s.get("tariff_subgroups", [])
                            ]
                            if not filtered_servers:
                                logger.warning(
                                    f"[Sync] В кластере {cluster_name} не найдено серверов для подгруппы "
                                    f"'{subgroup_title}'. Использую весь кластер."
                                )
                                filtered_servers = cluster_servers

                        if tariff and tariff.group_code:
                            group_code = tariff.group_code.lower()
                            if group_code in ALLOWED_GROUP_CODES:
                                special_filtered = [
                                    s for s in filtered_servers if group_code in (s.get("special_groups") or [])
                                ]
                                if special_filtered:
                                    filtered_servers = special_filtered
                                else:
                                    logger.warning(
                                        f"[Sync] В кластере {cluster_name} нет серверов со спецгруппой "
                                        f"'{group_code}'. Использую весь кластер."
                                    )

                        inbound_ids = [s["inbound_id"] for s in filtered_servers if s.get("inbound_id")]

                    if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                        raise Exception("Не удалось авторизоваться в Remnawave")

                    success = await remna.update_user(
                        uuid=key["client_id"],
                        expire_at=expire_iso,
                        telegram_id=key["tg_id"],
                        email=f"{key['email']}@fake.local",
                        active_user_inbounds=inbound_ids,
                        traffic_limit_bytes=traffic_limit_bytes,
                        hwid_device_limit=hwid_limit,
                    )

                    if success:
                        try:
                            sub = await remna.get_subscription_by_username(key["email"])
                            if sub:
                                new_remnawave_link = sub.get("subscriptionUrl")

                                if new_remnawave_link:
                                    servers = await get_servers(session)
                                    cluster_servers = servers.get(cluster_name, [])

                                    key_value = await make_aggregated_link(
                                        session=session,
                                        cluster_all=cluster_servers,
                                        cluster_id=cluster_name,
                                        email=key["email"],
                                        client_id=key["client_id"],
                                        tg_id=key["tg_id"],
                                        remna_link_override=None,
                                        plan=key["tariff_id"],
                                    )

                                    await session.execute(
                                        update(Key)
                                        .where(Key.tg_id == key["tg_id"], Key.client_id == key["client_id"])
                                        .values(remnawave_link=new_remnawave_link, key=key_value)
                                    )
                                    await session.commit()
                                    logger.info(f"[Sync] Обновлена ссылка для {key['email']}: {new_remnawave_link}")
                        except Exception as e:
                            logger.warning(f"[Sync] Не удалось получить ссылку для {key['email']}: {e}")

                    if not success:
                        logger.warning("[Sync] ошибка обновления, пробуем пересоздать")

                        await delete_key_from_cluster(cluster_name, key["email"], key["client_id"], session)

                        await session.execute(
                            delete(Key).where(Key.tg_id == key["tg_id"], Key.client_id == key["client_id"])
                        )

                        cluster_id_for_recreate = key["server_id"] if USE_COUNTRY_SELECTION else cluster_name
                        await create_key_on_cluster(
                            cluster_id_for_recreate,
                            key["tg_id"],
                            key["client_id"],
                            key["email"],
                            key["expiry_time"],
                            plan=key["tariff_id"],
                            session=session,
                            remnawave_link=key["remnawave_link"],
                        )

                    await asyncio.sleep(0.1)

                else:
                    await delete_key_from_cluster(cluster_name, key["email"], key["client_id"], session)

                    await session.execute(
                        delete(Key).where(Key.tg_id == key["tg_id"], Key.client_id == key["client_id"])
                    )

                    cluster_id_for_recreate = key["server_id"] if USE_COUNTRY_SELECTION else cluster_name
                    await create_key_on_cluster(
                        cluster_id_for_recreate,
                        key["tg_id"],
                        key["client_id"],
                        key["email"],
                        key["expiry_time"],
                        plan=key["tariff_id"],
                        session=session,
                        remnawave_link=key["remnawave_link"],
                    )

                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"[Sync] Ошибка при обработке ключа {key['client_id']} в {cluster_name}: {e}")

        await callback_query.message.edit_text(
            text=f"✅ Ключи успешно синхронизированы для кластера {cluster_name}",
            reply_markup=build_admin_back_kb("clusters"),
        )

    except Exception as e:
        logger.error(f"[Sync] Ошибка синхронизации кластера {cluster_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при синхронизации: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )
