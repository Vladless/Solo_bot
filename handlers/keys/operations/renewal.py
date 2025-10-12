import asyncio

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from database import (
    delete_notification,
    filter_cluster_by_subgroup,
    get_key_details,
    get_servers,
    resolve_device_limit_from_group,
    update_key_expiry,
    update_key_link,
)
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import extend_client_key, get_xui_instance
from panels.remnawave import RemnawaveAPI

from .aggregated_links import make_aggregated_link
from .subgroup_migration import migrate_between_subgroups


async def resolve_cluster(session: AsyncSession, cluster_id: str):
    servers = await get_servers(session)
    cluster = servers.get(cluster_id)
    if cluster:
        return cluster
    found = []
    for _key, server_list in servers.items():
        for s in server_list:
            if s.get("server_name", "").lower() == cluster_id.lower():
                found.append(s)
    if found:
        return found
    raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")


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
    remna = RemnawaveAPI(remnawave_nodes[0]["api_url"])
    if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
        logger.error(f"{PANEL_REMNA} Не удалось войти в Remnawave API")
        return False
    expire_iso = datetime.utcfromtimestamp(new_expiry_time // 1000).isoformat() + "Z"
    traffic_limit_bytes = total_gb * 1024 * 1024 * 1024 if total_gb else 0
    active_inbounds = [s["inbound_id"] for s in remnawave_nodes]
    updated = await remna.update_user(
        uuid=client_id,
        expire_at=expire_iso,
        active_user_inbounds=active_inbounds,
        traffic_limit_bytes=traffic_limit_bytes,
        hwid_device_limit=hwid_device_limit,
    )
    if updated:
        if reset_traffic:
            try:
                await remna.reset_user_traffic(client_id)
            except Exception as e:
                logger.warning(f"{PANEL_REMNA} reset_user_traffic: {e}")
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
):
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

        async def process_server(si, inbound, uniq, sub, name):
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
                    total_gb=traffic_bytes,
                    sub_id=sub,
                    tg_id=tg_id,
                    limit_ip=hwid_device_limit,
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
    hwid_device_limit: int = 0,
    reset_traffic: bool = True,
    target_subgroup: str | None = None,
    old_subgroup: str | None = None,
    plan=None,
):
    try:
        servers_map = await get_servers(session)

        kd = await get_key_details(session, email)
        if not kd or kd.get("client_id") != client_id:
            logger.error(f"Не найден ключ по email={email} и client_id={client_id}")
            return False

        tg_id = int(kd["tg_id"])
        server_id = kd["server_id"]

        single_server = None
        if servers_map.get(server_id):
            cluster = servers_map[server_id]
        else:
            for _k, sl in servers_map.items():
                for s in sl:
                    if s.get("server_name") == server_id:
                        single_server = s
                        break
                if single_server:
                    break
            cluster = (
                [single_server]
                if single_server
                else servers_map.get(cluster_id) or await resolve_cluster(session, cluster_id)
            )

        dl = await resolve_device_limit_from_group(session, server_id)
        if dl is not None:
            hwid_device_limit = dl

        if (target_subgroup or "") != (old_subgroup or "") and not single_server:
            new_client_id, remna_link = await migrate_between_subgroups(
                session=session,
                cluster_all=cluster,
                cluster_id=cluster_id,
                email=email,
                client_id=client_id,
                tg_id=tg_id,
                new_expiry_time=new_expiry_time,
                total_gb=total_gb,
                hwid_device_limit=hwid_device_limit,
                reset_traffic=reset_traffic,
                old_subgroup=old_subgroup,
                target_subgroup=target_subgroup,
            )

            await update_key_expiry(session, new_client_id or client_id, new_expiry_time)
            for prefix in ["key_24h", "key_10h", "key_expired", "renew"]:
                await delete_notification(session, tg_id, f"{email}_{prefix}")

            try:
                key_link = await make_aggregated_link(
                    session=session,
                    cluster_all=cluster,
                    cluster_id=cluster_id,
                    email=email,
                    client_id=new_client_id or client_id,
                    tg_id=tg_id,
                    subgroup_code=target_subgroup,
                    remna_link_override=remna_link,
                    plan=plan,
                )
                if key_link:
                    await update_key_link(session, email, key_link)
            except Exception as le:
                logger.warning(f"[Link] ошибка генерации/сохранения после миграции: {le}")

            return True

        if single_server:
            cluster_scope = [single_server]
        else:
            if target_subgroup:
                target = await filter_cluster_by_subgroup(session, cluster, target_subgroup, cluster_id)
                cluster_scope = target if target else cluster
            else:
                cluster_scope = cluster

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
        )

        if remna_ok or succeeded:
            await update_key_expiry(session, client_id, new_expiry_time)
            for prefix in ["key_24h", "key_10h", "key_expired", "renew"]:
                await delete_notification(session, tg_id, f"{email}_{prefix}")
            return True

        return False
    except Exception as e:
        logger.error(f"Не удалось продлить ключ {client_id} в кластере/на сервере {cluster_id}: {e}")
        raise
