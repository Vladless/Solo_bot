import asyncio

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import MODES_CONFIG
from database import update_key_client_id
from database.access.resolution import UserId, panel_identity_fields
from database.keys import resolve_key_operation_owner
from database.traffic_notifications import reset_traffic_notification_state
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
)
from settings.config import HAPP_CRYPTOLINK, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from utils.traffic_resources import traffic_resource_id


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
    session=None,
    billing_user_id: int | None = None,
    subscription_url: str | None = None,
    old_device_limit: int | None = None,
) -> tuple[str | None, str | None]:
    from panels.remnawave_runtime import remnawave_api
    from services.operations.renewal import renew_on_remnawave
    from services.operations.utils import bytes_from_gb

    if not servers:
        return None, None

    panel_tg = None
    panel_email = None
    if session is not None:
        try:
            owner = UserId(billing_user_id) if billing_user_id is not None else tg_id
            panel_tg, panel_email = await panel_identity_fields(session, owner)
        except Exception as e:
            logger.debug(f"{PANEL_REMNA} поля владельца не резолвлены: {e}")

    if total_gb is None:
        total_gb = 0
    if hwid_device_limit is None:
        hwid_device_limit = 0
    else:
        hwid_device_limit = int(hwid_device_limit)

    inbounds = [s.get("inbound_id") for s in servers if s.get("inbound_id")]

    async with remnawave_api(servers[0]["api_url"]) as api:
        if subscription_url:
            api.bind_subscription(client_id, email, subscription_url)
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
                updated = await renew_on_remnawave(
                    cluster=servers,
                    client_id=client_id,
                    email=email,
                    tg_id=tg_id,
                    new_expiry_time=new_expiry_time,
                    total_gb=total_gb,
                    hwid_device_limit=hwid_device_limit,
                    session=session,
                    reset_traffic=reset_traffic,
                    external_squad_uuid=external_squad_uuid,
                    old_device_limit=old_device_limit,
                    billing_user_id=billing_user_id,
                    subscription_url=subscription_url,
                    api=api,
                )
                if not updated:
                    return None, None

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
                    "activeInternalSquads": inbounds,
                    "uuid": client_id,
                }
                if panel_tg is not None:
                    payload["telegramId"] = panel_tg
                if panel_email:
                    payload["email"] = panel_email
                if traffic_bytes > 0:
                    payload["trafficLimitBytes"] = traffic_bytes
                payload["hwidDeviceLimit"] = hwid_device_limit
                if external_squad_uuid is not None:
                    payload["externalSquadUuid"] = external_squad_uuid or None

                created = await api.create_user(payload)
                if not created:
                    return None, None

                user = created.get("user") or created
                new_uuid = user.get("vlessUuid") or user.get("uuid")
                if str(new_uuid or "") != client_id or str(user.get("username") or "") != email:
                    return None, None

                sub_url = created.get("subscriptionUrl") or user.get("subscriptionUrl")
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
    session: AsyncSession | None = None,
    billing_user_id: int | None = None,
    reset_traffic: bool = True,
) -> bool:
    from services.operations.creation import create_client_on_server
    from services.operations.utils import bytes_from_gb

    panel_tg = tg_id
    if session is not None:
        owner = UserId(billing_user_id) if billing_user_id is not None else tg_id
        panel_tg, _ = await panel_identity_fields(session, owner)
    owner = UserId(billing_user_id) if billing_user_id is not None else tg_id
    semaphore = asyncio.Semaphore(2)
    tasks = [
        create_client_on_server(
            server,
            owner,
            client_id,
            email,
            new_expiry_time,
            semaphore,
            total_traffic_limit_bytes=bytes_from_gb(total_gb),
            device_limit_value=hwid_device_limit,
            panel_tg_id=panel_tg,
            reset_traffic=reset_traffic,
        )
        for server in servers
    ]
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if reset_traffic and session is not None and billing_user_id is not None:
            resource_ids = {
                traffic_resource_id(
                    "3x-ui",
                    server.get("api_url") or "",
                    inbound_id,
                    f"{email}_{str(server.get('server_name') or '').lower()}" if SUPERNODE else email,
                )
                for server, result in zip(servers, results, strict=True)
                if result is True
                for inbound_id in (None, server.get("inbound_id"))
            }
            current_owner = await resolve_key_operation_owner(session, billing_user_id, client_id, email)
            if current_owner is not None and resource_ids:
                await reset_traffic_notification_state(session, current_owner, client_id, resource_ids)
        return all(result is True for result in results)
    return False


async def migrate_between_subgroups(
    session: AsyncSession,
    target_servers: list,
    old_servers: list,
    email: str,
    client_id: str,
    tg_id: int,
    new_expiry_time: int,
    total_gb: int,
    hwid_device_limit: int,
    reset_traffic: bool,
    external_squad_uuid: str | None = None,
    billing_user_id: int | None = None,
    subscription_url: str | None = None,
    old_device_limit: int | None = None,
) -> tuple[str, str | None]:
    from services.operations.deletion import delete_on_3xui, delete_on_remnawave
    from services.operations.utils import norm_name, split_by_panel

    xui_tgt, remna_tgt = split_by_panel(target_servers)
    xui_old, remna_old = split_by_panel(old_servers)
    was_on_remna_before = bool(remna_old)

    xui_target_names = {norm_name(s.get("server_name")) for s in xui_tgt}
    remna_target_urls = {(s.get("api_url") or "").rstrip("/") for s in remna_tgt}

    xui_old_non = [s for s in xui_old if norm_name(s.get("server_name")) not in xui_target_names]
    remna_old_non = [s for s in remna_old if (s.get("api_url") or "").rstrip("/") not in remna_target_urls]

    if not target_servers:
        raise ValueError("Нет серверов в целевой подгруппе")

    if xui_tgt and not remna_tgt:
        xui_confirmed = await ensure_on_3xui(
            servers=xui_tgt,
            email=email,
            client_id=client_id,
            tg_id=tg_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            hwid_device_limit=hwid_device_limit,
            session=session,
            billing_user_id=billing_user_id,
            reset_traffic=reset_traffic,
        )
        if not xui_confirmed:
            raise ValueError("Перенос подписки не подтверждён на серверах целевой подгруппы")
        if xui_old_non:
            await delete_on_3xui(xui_old_non, email, client_id)
        if remna_old_non:
            await delete_on_remnawave(remna_old_non, client_id, username=email, subscription_url=subscription_url)
        return client_id, None

    if remna_tgt and not xui_tgt:
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
            session=session,
            billing_user_id=billing_user_id,
            subscription_url=subscription_url,
            old_device_limit=old_device_limit,
        )
        if not new_remna_id:
            raise ValueError("Перенос подписки не подтверждён на серверах целевой подгруппы")
        if xui_old_non:
            await delete_on_3xui(xui_old_non, email, client_id)
        if remna_old_non:
            await delete_on_remnawave(remna_old_non, client_id, username=email, subscription_url=subscription_url)
        if new_remna_id and new_remna_id != client_id:
            await update_key_client_id(session, email, new_remna_id)
            client_id = new_remna_id
        return client_id, remna_link

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
        session=session,
        billing_user_id=billing_user_id,
        subscription_url=subscription_url,
        old_device_limit=old_device_limit,
    )

    if not new_remna_id:
        raise ValueError("Перенос подписки не подтверждён на серверах целевой подгруппы")

    if new_remna_id and new_remna_id != old_id:
        client_id = new_remna_id
        await delete_on_3xui(xui_tgt, email, old_id)
        xui_confirmed = await ensure_on_3xui(
            servers=xui_tgt,
            email=email,
            client_id=client_id,
            tg_id=tg_id,
            new_expiry_time=new_expiry_time,
            total_gb=total_gb,
            hwid_device_limit=hwid_device_limit,
            session=session,
            billing_user_id=billing_user_id,
            reset_traffic=reset_traffic,
        )
        if not xui_confirmed:
            raise ValueError("Перенос подписки не подтверждён на серверах целевой подгруппы")
        await update_key_client_id(session, email, new_remna_id)
        if xui_old_non:
            await delete_on_3xui(xui_old_non, email, old_id)
        if remna_old_non:
            await delete_on_remnawave(remna_old_non, old_id, username=email, subscription_url=subscription_url)
        return client_id, remna_link

    xui_confirmed = await ensure_on_3xui(
        servers=xui_tgt,
        email=email,
        client_id=client_id,
        tg_id=tg_id,
        new_expiry_time=new_expiry_time,
        total_gb=total_gb,
        hwid_device_limit=hwid_device_limit,
        session=session,
        billing_user_id=billing_user_id,
        reset_traffic=reset_traffic,
    )
    if not new_remna_id or not xui_confirmed:
        raise ValueError("Перенос подписки не подтверждён на серверах целевой подгруппы")
    if xui_old_non:
        await delete_on_3xui(xui_old_non, email, old_id)
    if remna_old_non:
        await delete_on_remnawave(remna_old_non, old_id, username=email, subscription_url=subscription_url)
    return client_id, remna_link
