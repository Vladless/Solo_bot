import asyncio

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config import HAPP_CRYPTOLINK, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from database import filter_cluster_by_subgroup, update_key_client_id
from logger import logger
from panels._3xui import ClientConfig, add_client, extend_client_key, get_xui_instance
from panels.remnawave import RemnawaveAPI

from .deletion import delete_on_3xui, delete_on_remnawave
from .utils import bytes_from_gb, split_by_panel


async def ensure_on_remnawave(
    servers: list,
    email: str,
    client_id: str,
    tg_id: int,
    new_expiry_time: int,
    total_gb: int,
    hwid_device_limit: int,
    reset_traffic: bool,
) -> tuple[str | None, str | None]:
    if not servers:
        return None, None

    inbounds = [s.get("inbound_id") for s in servers if s.get("inbound_id")]
    server = servers[0]

    api = RemnawaveAPI(server["api_url"])
    ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
    if not ok:
        logger.warning("Remnawave API недоступен при создании/обновлении")
        return None, None

    expire_iso = datetime.utcfromtimestamp(new_expiry_time // 1000).isoformat() + "Z"
    traffic_bytes = bytes_from_gb(total_gb)

    try:
        updated = await api.update_user(
            uuid=client_id,
            expire_at=expire_iso,
            active_user_inbounds=inbounds,
            traffic_limit_bytes=traffic_bytes,
            hwid_device_limit=hwid_device_limit,
        )
        if updated:
            if reset_traffic:
                await api.reset_user_traffic(client_id)
            return client_id, None
    except Exception:
        pass

    try:
        payload = {
            "username": email,
            "trafficLimitStrategy": "NO_RESET",
            "expireAt": expire_iso,
            "telegramId": tg_id,
            "activeInternalSquads": inbounds,
        }
        if traffic_bytes > 0:
            payload["trafficLimitBytes"] = traffic_bytes
        if hwid_device_limit is not None:
            payload["hwidDeviceLimit"] = hwid_device_limit

        created = await api.create_user(payload)
        new_uuid = created.get("uuid") if isinstance(created, dict) else None
        remna_link = None
        if isinstance(created, dict):
            if HAPP_CRYPTOLINK:
                remna_link = (
                    created.get("happ", {}).get("cryptoLink") if isinstance(created.get("happ"), dict) else None
                )
            if not remna_link:
                remna_link = created.get("subscriptionUrl")
        return new_uuid, remna_link
    except Exception as e:
        logger.warning(f"Remnawave создание не удалось: {e}")
        return None, None


async def ensure_on_3xui(
    servers: list, email: str, client_id: str, tg_id: int, new_expiry_time: int, total_gb: int, hwid_device_limit: int
):
    tasks = []
    traffic = bytes_from_gb(total_gb)
    for s in servers:
        name = s.get("server_name", "unknown")
        inbound_id = s.get("inbound_id")
        if not inbound_id:
            logger.warning(f"[{name}] INBOUND_ID отсутствует")
            continue

        login_email = f"{email}_{name.lower()}" if SUPERNODE else email
        sub_id = email if SUPERNODE else login_email

        async def one(si, nm, inbound, login, sub):
            try:
                xui = await get_xui_instance(si["api_url"])
            except Exception as e:
                logger.warning(f"[{nm}] недоступна панель 3x-ui при создании/обновлении: {e}")
                return
            try:
                ok = await extend_client_key(
                    xui=xui,
                    inbound_id=int(inbound),
                    email=login,
                    new_expiry_time=new_expiry_time,
                    client_id=client_id,
                    total_gb=traffic,
                    sub_id=sub,
                    tg_id=tg_id,
                    limit_ip=hwid_device_limit,
                )
                if ok:
                    return
            except Exception as e:
                logger.info(f"[{nm}] extend_client_key не удалось ({e}) — пробую создать клиента")

            try:
                cfg = ClientConfig(
                    client_id=client_id,
                    email=login,
                    tg_id=tg_id,
                    limit_ip=hwid_device_limit,
                    total_gb=traffic,
                    expiry_time=new_expiry_time,
                    enable=True,
                    flow="xtls-rprx-vision",
                    inbound_id=int(inbound),
                    sub_id=sub,
                )
                created = await add_client(
                    xui,
                    cfg,
                )
                if not created:
                    logger.warning(f"[{nm}] add_client вернул False")
            except Exception as e:
                logger.warning(f"[{nm}] ошибка add_client: {e}")

        tasks.append(one(s, name, inbound_id, login_email, sub_id))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def migrate_between_subgroups(
    session: AsyncSession,
    cluster_all: list,
    cluster_id: str,
    email: str,
    client_id: str,
    tg_id: int,
    new_expiry_time: int,
    total_gb: int,
    hwid_device_limit: int,
    reset_traffic: bool,
    old_subgroup: str,
    target_subgroup: str,
) -> tuple[str, str | None]:
    target = await filter_cluster_by_subgroup(session, cluster_all, target_subgroup, cluster_id)
    target_names = {s.get("server_name") for s in target}
    non_target = [s for s in cluster_all if s.get("enabled", True) and s.get("server_name") not in target_names]

    xui_non, remna_non = split_by_panel(non_target)
    await delete_on_3xui(xui_non, email, client_id)
    await delete_on_remnawave(remna_non, client_id)

    if not target:
        logger.warning(f"[migrate] target_subgroup '{target_subgroup}' пуст — удалены внецелевые")
        return client_id, None

    xui_tgt, remna_tgt = split_by_panel(target)

    old_id = client_id
    new_remna_id, remna_link = await ensure_on_remnawave(
        servers=remna_tgt,
        email=email,
        client_id=client_id,
        tg_id=tg_id,
        new_expiry_time=new_expiry_time,
        total_gb=total_gb,
        hwid_device_limit=hwid_device_limit,
        reset_traffic=reset_traffic,
    )

    if new_remna_id and new_remna_id != old_id:
        await delete_on_3xui(xui_tgt, email, old_id)
        await update_key_client_id(session, email, new_remna_id)
        client_id = new_remna_id

    await ensure_on_3xui(
        servers=xui_tgt,
        email=email,
        client_id=client_id,
        tg_id=tg_id,
        new_expiry_time=new_expiry_time,
        total_gb=total_gb,
        hwid_device_limit=hwid_device_limit,
    )

    return client_id, remna_link
