import asyncio

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    delete_notification,
    get_key_details,
    get_servers,
    get_tariff_by_id,
    resolve_device_limit_from_group,
    update_key_expiry,
    update_key_link,
)
from database.access.resolution import UserId, panel_identity_fields, subscription_owner_ref
from database.keys import resolve_key_operation_owner
from database.traffic_notifications import reset_traffic_notification_state
from hooks.processors import process_get_cryptolink_after_renewal
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import extend_client_key, get_xui_instance
from panels.remnawave_runtime import invalidate_remnawave_profile_cache, with_remnawave_api
from services.errors import ValidationError
from services.renewal_access import resolve_key_renewal_scope, resolve_key_server_scope
from services.subscription_links import saved_public_subscription_tail
from settings.config import PUBLIC_LINK, SUPERNODE
from utils.traffic_resources import traffic_resource_id

from .aggregated_links import make_aggregated_link


async def renew_on_remnawave(
    cluster: list,
    client_id: str,
    email: str,
    tg_id: int,
    new_expiry_time: int,
    total_gb: int,
    hwid_device_limit: int,
    session: AsyncSession,
    reset_traffic: bool,
    target_server_name: str | None = None,
    external_squad_uuid: str | None = None,
    old_device_limit: int | None = None,
    billing_user_id: int | None = None,
    subscription_url: str | None = None,
    preserve_placement: bool = False,
    expiry_only: bool = False,
) -> bool:
    remnawave_nodes = [
        s for s in cluster if str(s.get("panel_type", "3x-ui")).lower() == "remnawave" and s.get("inbound_id")
    ]
    if not remnawave_nodes:
        return False
    if target_server_name:
        remnawave_nodes = [s for s in remnawave_nodes if s.get("server_name") == target_server_name] or remnawave_nodes[
            :1
        ]

    server_ref = remnawave_nodes[0].get("server_name") or remnawave_nodes[0].get("api_url") or ""
    api_url = remnawave_nodes[0].get("api_url")
    if not api_url:
        return False
    hwid_device_limit = int(hwid_device_limit or 0)

    expire_iso = datetime.utcfromtimestamp(new_expiry_time // 1000).isoformat() + "Z"
    traffic_limit_bytes = total_gb * 1024 * 1024 * 1024 if total_gb else 0
    active_inbounds = [s["inbound_id"] for s in remnawave_nodes]

    update_kwargs = {
        "uuid": client_id,
        "lookup_username": email,
        "expire_at": expire_iso,
        "active_user_inbounds": active_inbounds,
        "traffic_limit_bytes": traffic_limit_bytes,
        "hwid_device_limit": hwid_device_limit,
        "external_squad_uuid": external_squad_uuid,
    }
    if preserve_placement:
        update_kwargs.pop("active_user_inbounds")
        update_kwargs.pop("external_squad_uuid")
    if expiry_only:
        update_kwargs = {
            "uuid": client_id,
            "lookup_username": email,
            "expire_at": expire_iso,
            "status": None,
            "traffic_limit_bytes": None,
            "traffic_limit_strategy": None,
        }

    if session is not None and not expiry_only:
        try:
            owner = UserId(billing_user_id) if billing_user_id is not None else tg_id
            _panel_tg, _panel_email = await panel_identity_fields(session, owner)
            if _panel_tg is not None:
                update_kwargs["telegram_id"] = _panel_tg
            if _panel_email:
                update_kwargs["email"] = _panel_email
        except Exception as e:
            logger.debug(f"{PANEL_REMNA} поля владельца не резолвлены: {e}")

    traffic_was_reset = False

    async def _renew(api):
        nonlocal traffic_was_reset
        if not expiry_only and old_device_limit is not None and hwid_device_limit < old_device_limit:
            try:
                await api.clear_all_hwid_devices(client_id, username=email)
                logger.info(
                    f"{PANEL_REMNA} HWID устройства сброшены для {client_id} (лимит {old_device_limit} → {hwid_device_limit})"
                )
            except Exception as e:
                logger.warning(f"{PANEL_REMNA} Ошибка сброса HWID: {e}")

        updated_local = await api.update_user(**update_kwargs)
        if updated_local and reset_traffic and not expiry_only:
            try:
                traffic_was_reset = bool(await api.reset_user_traffic(client_id, username=email))
            except Exception as e:
                logger.warning(f"{PANEL_REMNA} reset_user_traffic: {e}")
        return bool(updated_local)

    updated = await with_remnawave_api(
        session,
        str(server_ref),
        _renew,
        timeout_sec=12.0,
        client_id=client_id,
        username=email,
        subscription_url=subscription_url,
        api_url=api_url,
    )
    if updated:
        try:
            await invalidate_remnawave_profile_cache(api_url=api_url, client_id=str(client_id))
        except Exception as e:
            logger.warning(f"{PANEL_REMNA} Не удалось сбросить кэш профиля {client_id}: {e}")
        if traffic_was_reset and session is not None and billing_user_id is not None:
            current_owner = await resolve_key_operation_owner(session, billing_user_id, client_id, email)
            if current_owner is not None:
                resource_id = traffic_resource_id("remnawave", remnawave_nodes[0].get("api_url") or "")
                await reset_traffic_notification_state(session, current_owner, client_id, {resource_id})
        logger.info(f"{PANEL_REMNA} Подписка {client_id} успешно продлена")
        return True
    logger.debug(f"{PANEL_REMNA} Не удалось продлить {client_id}. Автосоздание отключено.")
    return False


async def renew_on_3xui(
    cluster: list,
    email: str,
    client_id: str,
    new_expiry_time: int,
    total_gb: int,
    hwid_device_limit: int,
    tg_id: int,
    update_links: bool = False,
    target_server_name: str | None = None,
    session: AsyncSession | None = None,
    billing_user_id: int | None = None,
    reset_traffic: bool = True,
    expiry_only: bool = False,
):
    """Продлевает подписку на 3x-ui серверах кластера."""
    panel_tg = tg_id
    if session is not None and not expiry_only:
        owner = UserId(billing_user_id) if billing_user_id is not None else tg_id
        panel_tg, _ = await panel_identity_fields(session, owner)
    tasks = []
    for server_info in cluster:
        if target_server_name and server_info.get("server_name") != target_server_name:
            continue
        if str(server_info.get("panel_type", "3x-ui")).lower() != "3x-ui":
            continue
        inbound_id = server_info.get("inbound_id")
        server_name = server_info.get("server_name", "unknown")
        if not inbound_id:
            logger.warning(f"{PANEL_XUI} INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
            continue
        if SUPERNODE:
            unique_email = f"{email}_{server_name.lower()}"
            sub_id_val = email
        else:
            unique_email = email
            sub_id_val = unique_email
        traffic_bytes = total_gb * 1024 * 1024 * 1024 if total_gb else 0

        async def process_server(si, inbound, uniq, sub, name, traffic_limit_bytes=traffic_bytes):
            try:
                xui = await get_xui_instance(si["api_url"])
            except Exception as e:
                logger.warning(f"{PANEL_XUI} [{name}] API недоступен: {e}")
                return name, False, f"api_unavailable: {e}"
            try:
                updated = await extend_client_key(
                    xui=xui,
                    inbound_id=int(inbound),
                    email=uniq,
                    new_expiry_time=new_expiry_time,
                    client_id=client_id,
                    total_gb=traffic_limit_bytes,
                    sub_id=sub,
                    tg_id=panel_tg if panel_tg and int(panel_tg) > 0 else "",
                    limit_ip=hwid_device_limit,
                    reset_traffic=reset_traffic,
                    expiry_only=expiry_only,
                )
            except Exception as e:
                logger.warning(f"{PANEL_XUI} [{name}] ошибка продления: {e}")
                updated = False
            if updated:
                return name, True, None
            logger.debug(f"{PANEL_XUI} [{name}] не удалось обновить {uniq}. Автосоздание отключено.")
            return name, False, "no_autocreate"

        tasks.append(process_server(server_info, inbound_id, unique_email, sub_id_val, server_name))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    failed = []
    succeeded = []
    for r in results:
        if isinstance(r, Exception):
            failed.append(("unknown", f"task_exception: {r}"))
            continue
        name, ok, err = r
        if ok:
            succeeded.append(name)
        else:
            failed.append((name, err or "unknown_error"))
    if succeeded:
        if reset_traffic and not expiry_only and session is not None and billing_user_id is not None:
            current_owner = await resolve_key_operation_owner(session, billing_user_id, client_id, email)
            if current_owner is not None:
                resource_ids = {
                    traffic_resource_id(
                        "3x-ui",
                        server.get("api_url") or "",
                        server.get("inbound_id"),
                        f"{email}_{str(server.get('server_name') or '').lower()}" if SUPERNODE else email,
                    )
                    for server in cluster
                    if server.get("server_name") in succeeded
                }
                await reset_traffic_notification_state(session, current_owner, client_id, resource_ids)
        logger.info(f"{PANEL_XUI} продлено на: {', '.join(succeeded)}")
    if failed:
        logger.debug(f"{PANEL_XUI} не продлено на: " + ", ".join([f"{n} ({e})" for n, e in failed]))
    return succeeded, failed


async def renew_key_in_cluster(
    cluster_id: str,
    email: str,
    client_id: str,
    new_expiry_time: int,
    total_gb: int,
    session: AsyncSession,
    hwid_device_limit: int | None = None,
    reset_traffic: bool = True,
    target_subgroup: str | None = None,
    old_subgroup: str | None = None,
    plan=None,
    expiry_only: bool = False,
):
    """Продлевает ключ в кластере с учётом подгрупп и Remnawave/3x-ui."""
    try:
        kd = await get_key_details(session, email)
        if not kd or kd.get("client_id") != client_id:
            logger.error(f"Не найден ключ по email={email} и client_id={client_id}")
            return False
        if expiry_only and kd.get("is_frozen"):
            raise ValidationError("Сначала разморозьте подписку для изменения срока")

        billing_user_id = UserId(kd["user_id"])
        tg_id = saved_public_subscription_tail(kd.get("key"), email, PUBLIC_LINK)
        if tg_id is None:
            tg_id = await subscription_owner_ref(session, billing_user_id)
        server_id = kd["server_id"]

        servers_map = await get_servers(session, include_enabled=True)
        old_tariff = await get_tariff_by_id(session, kd["tariff_id"]) if kd.get("tariff_id") else None
        tariff = await get_tariff_by_id(session, plan) if plan is not None else old_tariff
        if expiry_only:
            scope = await resolve_key_server_scope(session, kd, servers_map=servers_map)
        elif tariff is not None or plan is not None:
            scope = await resolve_key_renewal_scope(session, kd, tariff, servers_map=servers_map)
        else:
            scope = await resolve_key_server_scope(session, kd, servers_map=servers_map)
            scope.servers = [server for server in scope.servers if server.get("enabled") is True]
            if not scope.servers or target_subgroup or old_subgroup:
                return False
        cluster_id = scope.cluster_name
        cluster_scope = scope.servers
        single_server = scope.selected_server
        server_id = single_server or server_id

        old_device_limit = kd.get("current_device_limit") or kd.get("selected_device_limit")
        if old_device_limit is None and old_tariff:
            old_device_limit = old_tariff.get("device_limit")

        external_squad_uuid = None
        if plan is not None:
            if tariff:
                raw_external_squad = tariff.get("external_squad")
                if raw_external_squad:
                    external_squad_uuid = raw_external_squad
                else:
                    external_squad_uuid = ""
                is_configurable = tariff.get("configurable", False)
                if not is_configurable and hwid_device_limit is None:
                    tariff_device_limit = tariff.get("device_limit")
                    if tariff_device_limit is not None:
                        hwid_device_limit = int(tariff_device_limit)
        elif hwid_device_limit is None:
            dl = await resolve_device_limit_from_group(session, server_id)
            if dl is not None:
                hwid_device_limit = dl
        if hwid_device_limit is None:
            hwid_device_limit = 0

        if (
            not expiry_only
            and not scope.preserve_placement
            and (target_subgroup or "") != (old_subgroup or "")
            and not single_server
        ):
            from services.tariffs.subgroup_migration import migrate_between_subgroups

            old_servers = []
            if old_tariff:
                try:
                    old_scope = await resolve_key_renewal_scope(
                        session, kd, {**old_tariff, "is_active": True}, servers_map=servers_map
                    )
                    old_servers = old_scope.servers
                except ValidationError as error:
                    logger.warning("[Renewal] Исходные серверы для удаления не определены: {}", error)

            new_client_id, remna_link = await migrate_between_subgroups(
                session=session,
                email=email,
                client_id=client_id,
                tg_id=tg_id,
                new_expiry_time=new_expiry_time,
                total_gb=total_gb,
                hwid_device_limit=hwid_device_limit,
                reset_traffic=reset_traffic,
                external_squad_uuid=external_squad_uuid,
                billing_user_id=billing_user_id,
                subscription_url=kd.get("remnawave_link") or kd.get("link"),
                target_servers=cluster_scope,
                old_servers=old_servers,
            )

            billing_user_id = await resolve_key_operation_owner(
                session, billing_user_id, new_client_id or client_id, email
            )
            if billing_user_id is None:
                return False
            await update_key_expiry(session, new_client_id or client_id, new_expiry_time, user_id=billing_user_id)
            for prefix in ["key_24h", "key_10h", "key_expired", "renew"]:
                await delete_notification(session, billing_user_id, f"{email}_{prefix}", commit=False)

            try:
                key_link = await make_aggregated_link(
                    session=session,
                    cluster_all=cluster_scope,
                    cluster_id=cluster_id,
                    email=email,
                    client_id=new_client_id or client_id,
                    tg_id=tg_id,
                    subgroup_code=None,
                    remna_link_override=remna_link,
                    plan=plan,
                )
                if key_link:
                    await update_key_link(session, email, key_link)
            except Exception as le:
                logger.warning(f"[Link] ошибка генерации/сохранения после миграции: {le}")

            return True

        remna_ok = await renew_on_remnawave(
            cluster=cluster_scope,
            client_id=client_id,
            email=email,
            tg_id=tg_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            hwid_device_limit=hwid_device_limit,
            session=session,
            reset_traffic=reset_traffic,
            target_server_name=server_id if single_server else None,
            external_squad_uuid=external_squad_uuid,
            old_device_limit=old_device_limit,
            billing_user_id=billing_user_id,
            subscription_url=kd.get("remnawave_link") or kd.get("link"),
            preserve_placement=scope.preserve_placement,
            expiry_only=expiry_only,
        )

        succeeded, _ = await renew_on_3xui(
            cluster=cluster_scope,
            email=email,
            client_id=client_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            hwid_device_limit=hwid_device_limit,
            tg_id=tg_id,
            update_links=False,
            target_server_name=server_id if single_server else None,
            session=session,
            billing_user_id=billing_user_id,
            reset_traffic=reset_traffic,
            expiry_only=expiry_only,
        )

        if remna_ok or succeeded:
            billing_user_id = await resolve_key_operation_owner(session, billing_user_id, client_id, email)
            if billing_user_id is None:
                return False
            await update_key_expiry(session, client_id, new_expiry_time, user_id=billing_user_id)
            for prefix in ["key_24h", "key_10h", "key_expired", "renew"]:
                await delete_notification(session, billing_user_id, f"{email}_{prefix}", commit=False)

            if expiry_only or scope.preserve_placement:
                return True
            try:
                remna_link_override = None
                if remna_ok and cluster_scope:
                    remnawave_nodes = [
                        s
                        for s in cluster_scope
                        if str(s.get("panel_type", "3x-ui")).lower() == "remnawave" and s.get("inbound_id")
                    ]
                    if remnawave_nodes:
                        remna_link_override = await process_get_cryptolink_after_renewal(
                            email=email,
                            cluster_id=cluster_id,
                            plan=plan,
                            session=session,
                            tg_id=kd.get("tg_id"),
                            remnawave_nodes=remnawave_nodes,
                        )

                key_link = await make_aggregated_link(
                    session=session,
                    cluster_all=cluster_scope,
                    cluster_id=cluster_id,
                    email=email,
                    client_id=client_id,
                    tg_id=tg_id,
                    subgroup_code=None,
                    remna_link_override=remna_link_override,
                    plan=plan,
                )
                if key_link:
                    await update_key_link(session, email, key_link)
            except Exception as le:
                logger.warning(f"[Link] ошибка генерации/сохранения после продления: {le}")

            return True

        return False
    except Exception as e:
        logger.error(f"Не удалось продлить ключ {client_id} в кластере/на сервере {cluster_id}: {e}")
        raise
