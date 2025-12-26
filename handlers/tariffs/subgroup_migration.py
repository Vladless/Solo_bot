import asyncio

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config import HAPP_CRYPTOLINK, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from core.bootstrap import MODES_CONFIG
from database import filter_cluster_by_subgroup, update_key_client_id
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import ClientConfig, add_client, extend_client_key, get_xui_instance
from panels.remnawave import RemnawaveAPI

from ..keys.operations.deletion import delete_on_3xui, delete_on_remnawave
from ..keys.operations.utils import bytes_from_gb, norm_name, split_by_panel


async def ensure_on_remnawave(
    servers: list,
    email: str,
    client_id: str,
    tg_id: int,
    new_expiry_time: int,
    total_gb: int,
    hwid_device_limit: int,
    reset_traffic: bool,
    attempt_update_first: bool,
    external_squad_uuid: str | None = None,
) -> tuple[str | None, str | None]:
    if not servers:
        return None, None

    if total_gb is None:
        total_gb = 0
    if hwid_device_limit is None:
        hwid_device_limit = 0
    else:
        hwid_device_limit = int(hwid_device_limit)

    inbounds = [s.get("inbound_id") for s in servers if s.get("inbound_id")]

    api = RemnawaveAPI(servers[0]["api_url"])
    ok = await api.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
    if not ok:
        logger.error(f"{PANEL_REMNA} API недоступен при создании/обновлении")
        return None, None

    expire_iso = datetime.utcfromtimestamp(new_expiry_time // 1000).isoformat() + "Z"
    traffic_bytes = bytes_from_gb(total_gb)
    use_crypto_link = bool(MODES_CONFIG.get("HAPP_CRYPTOLINK_ENABLED", HAPP_CRYPTOLINK))

    async def _build_link_from_subscription(sub_url: str | None) -> str | None:
        if not sub_url:
            return None
        remna_link = None
        if use_crypto_link:
            try:
                remna_link = await api.encrypt_happ_crypto_link(sub_url)
            except Exception as e:
                logger.error(f"{PANEL_REMNA} ошибка шифрования happ-ссылки: {e}")
                remna_link = None
        if not remna_link:
            remna_link = sub_url
        return remna_link

    async def do_update():
        try:
            updated = await api.update_user(
                uuid=client_id,
                expire_at=expire_iso,
                active_user_inbounds=inbounds,
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=hwid_device_limit,
                external_squad_uuid=external_squad_uuid,
            )
            if not updated:
                return None, None

            if reset_traffic:
                try:
                    await api.reset_user_traffic(client_id)
                except Exception:
                    pass

            remna_link = None
            try:
                sub_data = await api.get_subscription_by_username(email)
            except Exception as e:
                logger.error(f"{PANEL_REMNA} get_subscription_by_username при update: {e}")
                sub_data = None

            if sub_data:
                sub_url = sub_data.get("subscriptionUrl")
                remna_link = await _build_link_from_subscription(sub_url)

            return client_id, remna_link
        except Exception as e:
            logger.error(f"{PANEL_REMNA} update_user не удалось: {e}")
            return None, None

    async def do_create():
        try:
            payload = {
                "username": email,
                "trafficLimitStrategy": "NO_RESET",
                "expireAt": expire_iso,
                "telegramId": tg_id,
                "activeInternalSquads": inbounds,
                "uuid": client_id,
            }
            if traffic_bytes > 0:
                payload["trafficLimitBytes"] = traffic_bytes
            payload["hwidDeviceLimit"] = hwid_device_limit
            if external_squad_uuid is not None:
                payload["externalSquadUuid"] = external_squad_uuid or None

            created = await api.create_user(payload)
            if not created:
                return None, None

            user = created.get("user") or {}
            new_uuid = user.get("uuid") or client_id

            sub_url = created.get("subscriptionUrl")
            remna_link = await _build_link_from_subscription(sub_url)

            return new_uuid, remna_link
        except Exception as e:
            logger.error(f"{PANEL_REMNA} создание не удалось: {e}")
            return None, None

    if attempt_update_first:
        updated_id, link = await do_update()
        if updated_id:
            return updated_id, link
        return await do_create()

    created_id, link = await do_create()
    if created_id:
        return created_id, link
    return await do_update()


async def ensure_on_3xui(
    servers: list,
    email: str,
    client_id: str,
    tg_id: int,
    new_expiry_time: int,
    total_gb: int,
    hwid_device_limit: int,
    attempt_update_first: bool,
):
    tasks = []
    traffic = bytes_from_gb(total_gb)
    for s in servers:
        name = s.get("server_name", "unknown")
        inbound_id = s.get("inbound_id")
        if not inbound_id:
            logger.warning(f"{PANEL_XUI} [{name}] INBOUND_ID отсутствует")
            continue

        login_email = f"{email}_{name.lower()}" if SUPERNODE else email
        sub_id = email if SUPERNODE else login_email

        async def one(si, nm, inbound, login, sub):
            try:
                xui = await get_xui_instance(si["api_url"])
            except Exception as e:
                logger.error(f"{PANEL_XUI} [{nm}] API недоступен: {e}")
                return

            async def do_update():
                try:
                    updated = await extend_client_key(
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
                    return bool(updated)
                except Exception as e:
                    logger.error(f"{PANEL_XUI} [{nm}] ошибка продления: {e}")
                    return False

            async def do_create():
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
                    created = await add_client(xui, cfg)
                    if not created:
                        logger.error(f"{PANEL_XUI} [{nm}] add_client вернул False")
                    return bool(created)
                except Exception as e:
                    logger.error(f"{PANEL_XUI} [{nm}] ошибка add_client: {e}")
                    return False

            if attempt_update_first:
                if await do_update():
                    return
                await do_create()
            else:
                if await do_create():
                    return
                await do_update()

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
    external_squad_uuid: str | None = None,
) -> tuple[str, str | None]:
    target = await filter_cluster_by_subgroup(session, cluster_all, target_subgroup, cluster_id)
    xui_tgt, remna_tgt = split_by_panel(target)

    old_set = await filter_cluster_by_subgroup(session, cluster_all, old_subgroup, cluster_id)
    was_on_remna_before = any(
        s.get("enabled", True) and (s.get("panel_type", "").lower() == "remnawave") for s in old_set
    )
    was_on_xui_before = any(s.get("enabled", True) and (s.get("panel_type", "").lower() == "3x-ui") for s in old_set)

    xui_target_names = {norm_name(s.get("server_name")) for s in xui_tgt}
    remna_target_urls = {(s.get("api_url") or "").rstrip("/") for s in remna_tgt}

    xui_old = [s for s in old_set if s.get("enabled", True) and (s.get("panel_type", "").lower() == "3x-ui")]
    remna_old = [s for s in old_set if s.get("enabled", True) and (s.get("panel_type", "").lower() == "remnawave")]

    xui_old_non = [s for s in xui_old if norm_name(s.get("server_name")) not in xui_target_names]
    remna_old_non = [s for s in remna_old if (s.get("api_url") or "").rstrip("/") not in remna_target_urls]

    if not target:
        if xui_old:
            await delete_on_3xui(xui_old, email, client_id)
        if remna_old:
            await delete_on_remnawave(remna_old, client_id)
        return client_id, None

    if xui_tgt and not remna_tgt:
        await ensure_on_3xui(
            servers=xui_tgt,
            email=email,
            client_id=client_id,
            tg_id=tg_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            hwid_device_limit=hwid_device_limit,
            attempt_update_first=was_on_xui_before,
        )
        if xui_old_non:
            await delete_on_3xui(xui_old_non, email, client_id)
        if remna_old_non:
            await delete_on_remnawave(remna_old_non, client_id)
        return client_id, None

    if remna_tgt and not xui_tgt:
        if xui_old_non:
            await delete_on_3xui(xui_old_non, email, client_id)
        new_remna_id, remna_link = await ensure_on_remnawave(
            servers=remna_tgt,
            email=email,
            client_id=client_id,
            tg_id=tg_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            hwid_device_limit=hwid_device_limit,
            reset_traffic=reset_traffic,
            attempt_update_first=was_on_remna_before,
            external_squad_uuid=external_squad_uuid,
        )
        if remna_old_non:
            await delete_on_remnawave(remna_old_non, client_id)
        if new_remna_id and new_remna_id != client_id:
            await update_key_client_id(session, email, new_remna_id)
            client_id = new_remna_id
        return client_id, remna_link

    if xui_old_non:
        await delete_on_3xui(xui_old_non, email, client_id)

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
        attempt_update_first=was_on_remna_before,
        external_squad_uuid=external_squad_uuid,
    )

    if remna_old_non:
        await delete_on_remnawave(remna_old_non, old_id)

    if new_remna_id and new_remna_id != old_id:
        await update_key_client_id(session, email, new_remna_id)
        client_id = new_remna_id
        await delete_on_3xui(xui_tgt, email, old_id)
        await ensure_on_3xui(
            servers=xui_tgt,
            email=email,
            client_id=client_id,
            tg_id=tg_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            hwid_device_limit=hwid_device_limit,
            attempt_update_first=False,
        )
        return client_id, remna_link

    await ensure_on_3xui(
        servers=xui_tgt,
        email=email,
        client_id=client_id,
        tg_id=tg_id,
        new_expiry_time=new_expiry_time,
        total_gb=total_gb,
        hwid_device_limit=hwid_device_limit,
        attempt_update_first=was_on_xui_before,
    )
    return client_id, remna_link
