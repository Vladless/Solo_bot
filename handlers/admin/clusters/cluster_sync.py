import asyncio

from datetime import datetime, timezone
from typing import Any

from aiogram import F, types
from aiogram.types import CallbackQuery
from py3xui import AsyncApi
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from database import get_servers
from database.models import Key, Server, Tariff, User
from filters.admin import IsAdminFilter
from handlers.utils import ALLOWED_GROUP_CODES
from logger import logger
from panels.remnawave import RemnawaveAPI
from services.operations import (
    create_client_on_server,
    create_key_on_cluster,
    delete_key_from_cluster,
)
from services.operations.aggregated_links import make_aggregated_link
from settings.config import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    REMNAWAVE_LOGIN,
    REMNAWAVE_PASSWORD,
    USE_COUNTRY_SELECTION,
)
from utils.backup import create_backup_and_send_to_admins

from ..panel.keyboard import build_admin_back_kb
from .base import router
from .keyboard import AdminClusterCallback, build_availability_kb, build_sync_cluster_kb


SYNC_CONCURRENCY = 200


async def _fetch_all_panel_uuids(remna: RemnawaveAPI) -> set[str]:
    uuids: set[str] = set()
    page_size = 1000
    start = 0

    while True:
        try:
            r = await remna._request("GET", "/users", params={"size": page_size, "start": start})
        except Exception as e:
            logger.error(f"[Sync] GET /users error at start={start}: {e}")
            break

        if r.status_code != 200:
            logger.error(f"[Sync] GET /users returned {r.status_code}: {r.text[:200]}")
            break

        try:
            raw = r.json()
        except Exception as e:
            logger.error(f"[Sync] GET /users JSON parse error: {e}")
            break

        body = raw.get("response") or raw.get("data") or raw
        users = body.get("users") or []
        total = int(body.get("total") or 0)

        if not users:
            break

        for u in users:
            uid = u.get("uuid")
            if uid:
                uuids.add(str(uid))

        start += len(users)
        if len(users) < page_size or (total and start >= total):
            break

    return uuids


def _compute_user_fields(
    key,
    tariff: dict | None,
    cluster_servers: list[dict],
    use_country_selection: bool,
) -> tuple[int, int, str | None, list[str]]:
    traffic_limit_bytes = 0
    hwid_limit = 0
    subgroup_title = tariff.get("subgroup_title") if tariff else None

    current_device_limit_from_key = key.get("current_device_limit")
    current_traffic_limit_gb_from_key = key.get("current_traffic_limit")
    selected_device_limit_from_key = key.get("selected_device_limit")
    selected_traffic_limit_gb_from_key = key.get("selected_traffic_limit")

    if tariff:
        if current_traffic_limit_gb_from_key is not None:
            traffic_limit_bytes = int(current_traffic_limit_gb_from_key * 1024**3)
        elif selected_traffic_limit_gb_from_key is not None:
            traffic_limit_bytes = int(selected_traffic_limit_gb_from_key * 1024**3)
        elif tariff.get("traffic_limit") is not None:
            traffic_limit_bytes = int(tariff.get("traffic_limit") * 1024**3)

        if current_device_limit_from_key is not None:
            hwid_limit = int(current_device_limit_from_key)
        elif selected_device_limit_from_key is not None:
            hwid_limit = int(selected_device_limit_from_key)
        else:
            hwid_limit = int(tariff.get("device_limit") or 0)

    expire_iso: str | None = None
    if key.get("expiry_time"):
        expire_iso = datetime.utcfromtimestamp(key["expiry_time"] / 1000).replace(tzinfo=timezone.utc).isoformat()

    if use_country_selection:
        user_server = None
        for s in cluster_servers:
            if s.get("server_name") == key["server_id"]:
                user_server = s
                break
        inbound_ids = [user_server["inbound_id"]] if user_server and user_server.get("inbound_id") else []
    else:
        filtered_servers = cluster_servers
        if subgroup_title or (tariff and tariff.get("id")):
            tid = tariff.get("id") if tariff else None
            filtered_servers = [
                s
                for s in cluster_servers
                if (tid and tid in (s.get("tariff_ids") or []))
                or (subgroup_title and subgroup_title in (s.get("tariff_subgroups") or []))
            ]
            if not filtered_servers:
                filtered_servers = cluster_servers

        if tariff and tariff.get("group_code"):
            group_code = tariff.get("group_code").lower()
            if group_code in ALLOWED_GROUP_CODES:
                special_filtered = [s for s in filtered_servers if group_code in (s.get("special_groups") or [])]
                if special_filtered:
                    filtered_servers = special_filtered

        inbound_ids = [s["inbound_id"] for s in filtered_servers if s.get("inbound_id")]

    return traffic_limit_bytes, hwid_limit, expire_iso, inbound_ids


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
                unique_nodes = []
                for node_info in nodes_info:
                    node_name = node_info.get("name", "Unknown")
                    if node_name in seen:
                        continue
                    seen.add(node_name)
                    unique_nodes.append(node_info)

                unique_nodes.sort(key=lambda n: n.get("online_users", 0), reverse=True)

                for node_info in unique_nodes:
                    node_name = node_info.get("name", "Unknown")
                    country_code = node_info.get("country_code", "Unknown")
                    online_users = node_info.get("online_users", 0)

                    flag = (
                        "".join(chr(ord(c) + 127397) for c in country_code.upper())
                        if country_code != "Unknown" and len(country_code) == 2
                        else country_code
                    )
                    status = "🔴 " if not node_info.get("is_online", True) else ""
                    result_text += f"  ↳ {status}{flag} ({node_name}): {online_users} онлайн\n"

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

        use_country_selection = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))

        if use_country_selection:
            stmt = (
                select(
                    Server.api_url,
                    Server.inbound_id,
                    Server.server_name,
                    Server.panel_type,
                    Key.user_id,
                    User.tg_id.label("owner_tg_id"),
                    Key.client_id,
                    Key.email,
                    Key.expiry_time,
                    Key.tariff_id,
                    Key.remnawave_link,
                    Key.selected_device_limit,
                    Key.selected_traffic_limit,
                    Key.current_device_limit,
                    Key.current_traffic_limit,
                )
                .join(Key, Server.server_name == Key.server_id)
                .join(User, Key.user_id == User.id)
                .where(Server.server_name == server_name)
            )
        else:
            stmt = (
                select(
                    Server.api_url,
                    Server.inbound_id,
                    Server.server_name,
                    Server.panel_type,
                    Key.user_id,
                    User.tg_id.label("owner_tg_id"),
                    Key.client_id,
                    Key.email,
                    Key.expiry_time,
                    Key.tariff_id,
                    Key.remnawave_link,
                    Key.selected_device_limit,
                    Key.selected_traffic_limit,
                    Key.current_device_limit,
                    Key.current_traffic_limit,
                )
                .join(Key, Server.cluster_name == Key.server_id)
                .join(User, Key.user_id == User.id)
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

        all_servers = await get_servers(session)
        cluster_servers = all_servers.get(cluster_name, [])

        server_info = None
        for s in cluster_servers:
            if s.get("server_name") == server_name:
                server_info = s
                break

        tariff_ids = {key["tariff_id"] for key in keys_to_sync if key["tariff_id"]}
        tariffs_cache = {}
        if tariff_ids:
            tariffs_result = await session.execute(select(Tariff).where(Tariff.id.in_(tariff_ids)))
            tariffs_list = tariffs_result.scalars().all()
            tariffs_cache = {t.id: dict(t.__dict__) for t in tariffs_list}

        semaphore = asyncio.Semaphore(2)
        for key in keys_to_sync:
            try:
                if key["panel_type"] == "remnawave":
                    tariff = tariffs_cache.get(key["tariff_id"]) if key["tariff_id"] else None

                    if tariff and server_info:
                        subgroup = tariff.get("subgroup_title")
                        tid = key["tariff_id"]
                        has_new_binding = tid and tid in (server_info.get("tariff_ids") or [])
                        has_old_binding = subgroup and subgroup in (server_info.get("tariff_subgroups") or [])
                        has_any_binding = bool(server_info.get("tariff_ids") or server_info.get("tariff_subgroups"))

                        if has_any_binding and subgroup and not has_new_binding and not has_old_binding:
                            continue

                        if tariff.get("group_code") and tariff.get("group_code").lower() in ALLOWED_GROUP_CODES:
                            if tariff.get("group_code").lower() not in server_info.get("special_groups", []):
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

                    current_device_limit_from_key = key.get("current_device_limit")
                    current_traffic_limit_gb_from_key = key.get("current_traffic_limit")
                    selected_device_limit_from_key = key.get("selected_device_limit")
                    selected_traffic_limit_gb_from_key = key.get("selected_traffic_limit")

                    if tariff:
                        if current_traffic_limit_gb_from_key is not None:
                            traffic_limit_bytes = int(current_traffic_limit_gb_from_key * 1024**3)
                        elif selected_traffic_limit_gb_from_key is not None:
                            traffic_limit_bytes = int(selected_traffic_limit_gb_from_key * 1024**3)
                        elif tariff.get("traffic_limit") is not None:
                            traffic_limit_bytes = int(tariff.get("traffic_limit") * 1024**3)

                        if current_device_limit_from_key is not None:
                            hwid_limit = int(current_device_limit_from_key)
                        elif selected_device_limit_from_key is not None:
                            hwid_limit = int(selected_device_limit_from_key)
                        else:
                            hwid_limit = tariff.get("device_limit")

                    from database.access.resolution import panel_identity_fields

                    _ptg, _pemail = await panel_identity_fields(
                        session, int(key.get("owner_tg_id") or 0) or key["user_id"]
                    )
                    success = await remna.update_user(
                        uuid=key["client_id"],
                        expire_at=expire_iso,
                        telegram_id=_ptg,
                        email=_pemail or None,
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
                                    key_value = await make_aggregated_link(
                                        session=session,
                                        cluster_all=cluster_servers,
                                        cluster_id=cluster_name,
                                        email=key["email"],
                                        client_id=key["client_id"],
                                        tg_id=int(key.get("owner_tg_id") or 0) or key["user_id"],
                                        remna_link_override=None,
                                        plan=tariff,
                                    )

                                    await session.execute(
                                        update(Key)
                                        .where(Key.user_id == key["user_id"], Key.client_id == key["client_id"])
                                        .values(remnawave_link=new_remnawave_link, key=key_value)
                                    )
                                    logger.info(f"[Sync] Обновлена ссылка для {key['email']}: {new_remnawave_link}")
                        except Exception as e:
                            logger.warning(f"[Sync] Не удалось получить ссылку для {key['email']}: {e}")

                    if not success:
                        logger.warning("[Sync] ошибка обновления, пробуем пересоздать")

                        await delete_key_from_cluster(server_name, key["email"], key["client_id"], session)

                        await create_key_on_cluster(
                            cluster_id=server_name,
                            tg_id=int(key.get("owner_tg_id") or 0) or key["user_id"],
                            client_id=key["client_id"],
                            email=key["email"],
                            expiry_timestamp=key["expiry_time"],
                            plan=key["tariff_id"],
                            session=session,
                            remnawave_link=key["remnawave_link"],
                            hwid_limit=hwid_limit,
                            traffic_limit_bytes=traffic_limit_bytes,
                            selected_device_limit=key.get("selected_device_limit"),
                            selected_traffic_limit_gb=key.get("selected_traffic_limit"),
                            current_device_limit=key.get("current_device_limit"),
                            current_traffic_limit_gb=key.get("current_traffic_limit"),
                            selected_price_rub=key.get("selected_price_rub"),
                        )
                else:
                    await create_client_on_server(
                        {
                            "api_url": key["api_url"],
                            "inbound_id": key["inbound_id"],
                            "server_name": key["server_name"],
                        },
                        int(key.get("owner_tg_id") or 0) or key["user_id"],
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

        use_country_selection = bool(MODES_CONFIG.get("COUNTRY_SELECTION_ENABLED", USE_COUNTRY_SELECTION))

        if use_country_selection:
            server_names = [s.get("server_name") for s in cluster_servers if s.get("server_name")]
            if not server_names:
                await callback_query.message.edit_text(
                    text=f"❌ В кластере {cluster_name} нет серверов.",
                    reply_markup=build_admin_back_kb("clusters"),
                )
                return
            result = await session.execute(
                select(
                    Key.user_id,
                    User.tg_id.label("owner_tg_id"),
                    Key.client_id,
                    Key.email,
                    Key.expiry_time,
                    Key.remnawave_link,
                    Key.tariff_id,
                    Key.server_id,
                    Key.selected_device_limit,
                    Key.selected_traffic_limit,
                    Key.current_device_limit,
                    Key.current_traffic_limit,
                )
                .join(User, Key.user_id == User.id)
                .where(Key.server_id.in_(server_names), Key.is_frozen.is_(False))
            )
        else:
            result = await session.execute(
                select(
                    Key.user_id,
                    User.tg_id.label("owner_tg_id"),
                    Key.client_id,
                    Key.email,
                    Key.expiry_time,
                    Key.remnawave_link,
                    Key.tariff_id,
                    Key.server_id,
                    Key.selected_device_limit,
                    Key.selected_traffic_limit,
                    Key.current_device_limit,
                    Key.current_traffic_limit,
                )
                .join(User, Key.user_id == User.id)
                .where(Key.server_id == cluster_name, Key.is_frozen.is_(False))
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

        tariff_ids = {key["tariff_id"] for key in keys_to_sync if key["tariff_id"]}
        tariffs_cache = {}
        if tariff_ids:
            tariffs_result = await session.execute(select(Tariff).where(Tariff.id.in_(tariff_ids)))
            tariffs_list = tariffs_result.scalars().all()
            tariffs_cache = {t.id: dict(t.__dict__) for t in tariffs_list}

        if only_remnawave:
            total_keys = len(keys_to_sync)

            api_url = cluster_servers[0]["api_url"]
            remna = RemnawaveAPI(api_url)
            login_ok = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
            if not login_ok:
                await callback_query.message.edit_text(
                    text=f"❌ Не удалось авторизоваться в Remnawave для кластера {cluster_name}.",
                    reply_markup=build_admin_back_kb("clusters"),
                )
                return

            try:
                await callback_query.message.edit_text(
                    text=(
                        f"<b>🔄 Синхронизация кластера {cluster_name}</b>\n\n"
                        f"🔑 Ключей в БД: <b>{total_keys}</b>\n\n"
                        "📥 Получение списка пользователей с панели..."
                    )
                )
                panel_uuids = await _fetch_all_panel_uuids(remna)
                logger.info(f"[Sync] В панели {cluster_name}: {len(panel_uuids)} юзеров")

                to_update: list = []
                to_create: list = []
                for key in keys_to_sync:
                    if str(key["client_id"]) in panel_uuids:
                        to_update.append(key)
                    else:
                        to_create.append(key)

                logger.info(f"[Sync] {cluster_name}: к update={len(to_update)}, к create={len(to_create)}")

                semaphore = asyncio.Semaphore(SYNC_CONCURRENCY)
                pending_db_updates: list[dict] = []
                pending_lock = asyncio.Lock()
                stats = {"updated": 0, "created": 0, "failed": 0, "done": 0}

                async def process_update(key):
                    async with semaphore:
                        try:
                            tariff = tariffs_cache.get(key["tariff_id"]) if key["tariff_id"] else None
                            traffic_limit_bytes, hwid_limit, expire_iso, inbound_ids = _compute_user_fields(
                                key, tariff, cluster_servers, use_country_selection
                            )

                            if use_country_selection and not inbound_ids:
                                stats["failed"] += 1
                                logger.warning(f"[Sync] update {key.get('email')}: server not found")
                                return

                            from database.access.resolution import panel_identity_fields

                            _ptg, _pemail = await panel_identity_fields(
                                session, int(key.get("owner_tg_id") or 0) or key["user_id"]
                            )
                            success = await remna.update_user(
                                uuid=key["client_id"],
                                expire_at=expire_iso,
                                telegram_id=_ptg,
                                email=_pemail or None,
                                active_user_inbounds=inbound_ids,
                                traffic_limit_bytes=traffic_limit_bytes,
                                hwid_device_limit=hwid_limit,
                            )

                            if not success:
                                stats["failed"] += 1
                                logger.warning(f"[Sync] update_user failed for {key.get('email')}")
                                return

                            sub = await remna.get_subscription_by_username(key["email"])
                            new_link = sub.get("subscriptionUrl") if sub else None

                            stats["updated"] += 1
                            if new_link:
                                async with pending_lock:
                                    pending_db_updates.append({
                                        "key": key,
                                        "tariff": tariff,
                                        "new_link": new_link,
                                    })
                        except Exception as e:
                            stats["failed"] += 1
                            logger.error(f"[Sync] update error for {key.get('email')}: {e}")
                        finally:
                            stats["done"] += 1

                async def process_create(key):
                    async with semaphore:
                        try:
                            tariff = tariffs_cache.get(key["tariff_id"]) if key["tariff_id"] else None
                            traffic_limit_bytes, hwid_limit, expire_iso, inbound_ids = _compute_user_fields(
                                key, tariff, cluster_servers, use_country_selection
                            )

                            if not expire_iso:
                                stats["failed"] += 1
                                logger.warning(f"[Sync] create {key.get('email')}: no expiry_time")
                                return

                            payload = {
                                "uuid": str(key["client_id"]),
                                "username": key["email"],
                                "expireAt": expire_iso,
                                "status": "ACTIVE",
                                "trafficLimitStrategy": "NO_RESET",
                                "trafficLimitBytes": traffic_limit_bytes,
                                "hwidDeviceLimit": hwid_limit,
                            }
                            from database.access.resolution import panel_identity_fields

                            _ptg, _pemail = await panel_identity_fields(
                                session, int(key.get("owner_tg_id") or 0) or key["user_id"]
                            )
                            if _ptg is not None:
                                payload["telegramId"] = _ptg
                            if _pemail:
                                payload["email"] = _pemail
                            if inbound_ids:
                                payload["activeInternalSquads"] = inbound_ids

                            stored_link = key.get("remnawave_link")
                            if stored_link and "/" in stored_link:
                                payload["shortUuid"] = stored_link.rstrip("/").split("/")[-1]

                            r = await remna._request("POST", "/users", json=payload)
                            if r.status_code not in (200, 201):
                                stats["failed"] += 1
                                logger.warning(
                                    f"[Sync] create failed for {key.get('email')}: {r.status_code} {r.text[:200]}"
                                )
                                return

                            sub = await remna.get_subscription_by_username(key["email"])
                            new_link = sub.get("subscriptionUrl") if sub else None

                            stats["created"] += 1
                            if new_link:
                                async with pending_lock:
                                    pending_db_updates.append({
                                        "key": key,
                                        "tariff": tariff,
                                        "new_link": new_link,
                                    })
                        except Exception as e:
                            stats["failed"] += 1
                            logger.error(f"[Sync] create error for {key.get('email')}: {e}")
                        finally:
                            stats["done"] += 1

                async def progress_loop():
                    while True:
                        await asyncio.sleep(3)
                        done = stats["done"]
                        if total_keys == 0:
                            return
                        percent = int((done / total_keys) * 100)
                        bar = "█" * (percent // 5) + "░" * (20 - percent // 5)
                        try:
                            await callback_query.message.edit_text(
                                text=(
                                    f"<b>🔄 Синхронизация кластера {cluster_name}</b>\n\n"
                                    f"🔑 Всего: <b>{total_keys}</b> "
                                    f"(update: {len(to_update)}, create: {len(to_create)})\n\n"
                                    f"Готово: <b>{done}/{total_keys}</b> ({percent}%)\n"
                                    f"<code>{bar}</code>\n\n"
                                    f"✏️ Обновлено: {stats['updated']}\n"
                                    f"➕ Создано: {stats['created']}\n"
                                    f"❌ Ошибок: {stats['failed']}"
                                )
                            )
                        except Exception:
                            pass

                progress_task = asyncio.create_task(progress_loop())
                try:
                    tasks = [process_update(k) for k in to_update] + [process_create(k) for k in to_create]
                    await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except (asyncio.CancelledError, Exception):
                        pass

                logger.info(
                    f"[Sync] HTTP-фаза завершена: updated={stats['updated']}, "
                    f"created={stats['created']}, failed={stats['failed']}"
                )

                bulk_updates: list[dict] = []
                for item in pending_db_updates:
                    key = item["key"]
                    try:
                        key_value = await make_aggregated_link(
                            session=session,
                            cluster_all=cluster_servers,
                            cluster_id=cluster_name,
                            email=key["email"],
                            client_id=key["client_id"],
                            tg_id=int(key.get("owner_tg_id") or 0) or key["user_id"],
                            remna_link_override=None,
                            plan=item["tariff"],
                        )
                        bulk_updates.append({
                            "user_id": key["user_id"],
                            "client_id": key["client_id"],
                            "remnawave_link": item["new_link"],
                            "key": key_value,
                        })
                    except Exception as e:
                        logger.error(f"[Sync] make_aggregated_link error for {key.get('email')}: {e}")

                if bulk_updates:
                    try:
                        await session.run_sync(
                            lambda sync_session: sync_session.bulk_update_mappings(Key, bulk_updates)
                        )
                        logger.info(f"[Sync] Bulk: обновлено {len(bulk_updates)} ключей в БД")
                    except Exception as bulk_error:
                        logger.warning(f"[Sync] Bulk упал, fallback: {bulk_error}")
                        await session.rollback()

                        for upd in bulk_updates:
                            try:
                                await session.execute(
                                    update(Key)
                                    .where(
                                        Key.user_id == upd["user_id"],
                                        Key.client_id == upd["client_id"],
                                    )
                                    .values(remnawave_link=upd["remnawave_link"], key=upd["key"])
                                )
                            except Exception as e:
                                logger.error(f"[Sync] Fallback ошибка {upd['client_id']}: {e}")
                                await session.rollback()
            finally:
                await remna.aclose()

        else:
            for key in keys_to_sync:
                try:
                    traffic_limit_bytes = 0
                    hwid_limit = 0
                    tariff = tariffs_cache.get(key["tariff_id"]) if key["tariff_id"] else None

                    current_device_limit_from_key = key.get("current_device_limit")
                    current_traffic_limit_gb_from_key = key.get("current_traffic_limit")
                    selected_device_limit_from_key = key.get("selected_device_limit")
                    selected_traffic_limit_gb_from_key = key.get("selected_traffic_limit")

                    if tariff:
                        if current_traffic_limit_gb_from_key is not None:
                            traffic_limit_bytes = int(current_traffic_limit_gb_from_key * 1024**3)
                        elif selected_traffic_limit_gb_from_key is not None:
                            traffic_limit_bytes = int(selected_traffic_limit_gb_from_key * 1024**3)
                        elif tariff.get("traffic_limit") is not None:
                            traffic_limit_bytes = int(tariff.get("traffic_limit") * 1024**3)
                        else:
                            traffic_limit_bytes = 0

                        if current_device_limit_from_key is not None:
                            hwid_limit = int(current_device_limit_from_key)
                        elif selected_device_limit_from_key is not None:
                            hwid_limit = int(selected_device_limit_from_key)
                        else:
                            hwid_limit = tariff.get("device_limit")

                        tariff.get("subgroup_title")
                    elif key["tariff_id"]:
                        logger.warning(
                            f"[Sync] Ключ {key['client_id']} с несуществующим тарифом ID={key['tariff_id']} — "
                            f"обновим без лимитов"
                        )

                    await delete_key_from_cluster(cluster_name, key["email"], key["client_id"], session)

                    await session.execute(
                        delete(Key).where(Key.user_id == key["user_id"], Key.client_id == key["client_id"])
                    )

                    cluster_id_for_recreate = key["server_id"] if use_country_selection else cluster_name
                    await create_key_on_cluster(
                        cluster_id_for_recreate,
                        int(key.get("owner_tg_id") or 0) or key["user_id"],
                        key["client_id"],
                        key["email"],
                        key["expiry_time"],
                        plan=key["tariff_id"],
                        session=session,
                        remnawave_link=key["remnawave_link"],
                        hwid_limit=hwid_limit,
                        traffic_limit_bytes=traffic_limit_bytes,
                        selected_device_limit=key.get("selected_device_limit"),
                        selected_traffic_limit_gb=key.get("selected_traffic_limit"),
                        current_device_limit=key.get("current_device_limit"),
                        current_traffic_limit_gb=key.get("current_traffic_limit"),
                        selected_price_rub=key.get("selected_price_rub"),
                    )

                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error(f"[Sync] Ошибка при обработке ключа {key['client_id']} в {cluster_name}: {e}")

        await callback_query.message.edit_text(
            text=(
                f"✅ <b>Синхронизация завершена</b>\n\n"
                f"📊 Кластер: <b>{cluster_name}</b>\n"
                f"🔑 Обработано ключей: <b>{len(keys_to_sync)}</b>"
            ),
            reply_markup=build_admin_back_kb("clusters"),
        )

    except Exception as e:
        logger.error(f"[Sync] Ошибка синхронизации кластера {cluster_name}: {e}")
        await callback_query.message.edit_text(
            text=f"❌ Произошла ошибка при синхронизации: {e}",
            reply_markup=build_admin_back_kb("clusters"),
        )
