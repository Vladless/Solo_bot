import asyncio

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from database import filter_cluster_by_subgroup, filter_cluster_by_tariff, get_servers, get_tariff_by_id, store_key
from database.access.resolution import UserId, panel_identity_fields, subscription_owner_ref, user_id_from_legacy_ref
from database.users import mark_trial_started_if_eligible
from hooks.processors import process_extract_cryptolink_from_result
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import ClientConfig, _client_snapshot, _update_client, add_client, get_xui_instance
from settings.config import PUBLIC_LINK, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE

from .aggregated_links import make_aggregated_link


async def create_key_on_cluster(
    cluster_id: str,
    tg_id: int,
    client_id: str,
    email: str,
    expiry_timestamp: int,
    plan: int = None,
    session: AsyncSession = None,
    remnawave_link: str = None,
    hwid_limit: int = None,
    traffic_limit_bytes: int = None,
    is_trial: bool = False,
    selected_device_limit: int = None,
    selected_traffic_limit_gb: int = None,
    current_device_limit: int = None,
    current_traffic_limit_gb: int = None,
    selected_price_rub: int = None,
    reuse_existing: bool = False,
):
    from panels.remnawave import get_vless_link_for_remnawave_by_username
    from panels.remnawave_runtime import remnawave_api
    from services.clusters import ALLOWED_GROUP_CODES, check_server_key_limit

    try:
        owner_ref = await user_id_from_legacy_ref(session, tg_id)
        if owner_ref is None:
            raise ValueError(f"Пользователь не найден для ключа: {tg_id}")
        tg_id = owner_ref
        panel_tg, panel_email = await panel_identity_fields(session, tg_id)
        servers = await get_servers(session)
        cluster = servers.get(cluster_id)
        server_id_to_store = cluster_id

        if not cluster:
            found_servers = []
            for _key, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        found_servers.append(server_info)
            if found_servers:
                cluster = found_servers
                server_id_to_store = found_servers[0].get("server_name")
            else:
                raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")

        enabled_servers = [s for s in cluster if s.get("enabled", True)]
        if not enabled_servers:
            raise ValueError(f"Нет доступных серверов в кластере {cluster_id}")

        tariff = None
        subgroup_title = None
        need_vless_key = False

        traffic_limit_bytes_value = 0
        device_limit_value = 0
        external_squad_uuid = None

        if plan is not None:
            tariff = await get_tariff_by_id(session, plan)
            if not tariff:
                raise ValueError(f"Тариф с id={plan} не найден.")

            if traffic_limit_bytes is None:
                raw_traffic_limit = tariff.get("traffic_limit")
                if raw_traffic_limit:
                    traffic_limit_bytes_value = int(raw_traffic_limit) * 1024 * 1024 * 1024
                else:
                    traffic_limit_bytes_value = 0
            else:
                traffic_limit_bytes_value = int(traffic_limit_bytes)

            if hwid_limit is None:
                raw_device_limit = tariff.get("device_limit")
                device_limit_value = int(raw_device_limit) if raw_device_limit is not None else 0
            else:
                device_limit_value = int(hwid_limit)

            subgroup_title = tariff.get("subgroup_title")
            need_vless_key = bool(tariff.get("vless"))
            external_squad_uuid = tariff.get("external_squad") or None
        else:
            traffic_limit_bytes_value = int(traffic_limit_bytes or 0)
            device_limit_value = int(hwid_limit or 0)

        if plan is not None:
            filtered = await filter_cluster_by_tariff(session, enabled_servers, plan, cluster_id)
            if filtered is not enabled_servers:
                enabled_servers = filtered
            elif subgroup_title:
                enabled_servers = await filter_cluster_by_subgroup(
                    session, enabled_servers, subgroup_title, cluster_id, tariff_id=plan
                )
        elif subgroup_title:
            enabled_servers = await filter_cluster_by_subgroup(
                session, enabled_servers, subgroup_title, cluster_id, tariff_id=plan
            )

        if not enabled_servers:
            raise ValueError(f"Нет серверов после фильтрации по привязкам в кластере {cluster_id}")

        special = None
        if is_trial:
            special = "trial"
        elif tariff:
            gc = (tariff.get("group_code") or "").lower()
            if gc in ALLOWED_GROUP_CODES:
                special = gc

        if special:
            bound_servers = [s for s in enabled_servers if special in (s.get("special_groups") or [])]
            if bound_servers:
                enabled_servers = bound_servers
            else:
                logger.info(
                    f"[Key Creation] В кластере {cluster_id} нет серверов со спецгруппой '{special}'. "
                    f"Использую весь кластер."
                )

        remnawave_servers = [
            s
            for s in enabled_servers
            if s.get("panel_type", "3x-ui").lower() == "remnawave" and await check_server_key_limit(s, session)
        ]
        xui_servers = [
            s
            for s in enabled_servers
            if s.get("panel_type", "3x-ui").lower() == "3x-ui" and await check_server_key_limit(s, session)
        ]

        if not remnawave_servers and not xui_servers:
            raise ValueError(f"Нет серверов с доступным лимитом в кластере {cluster_id}")

        semaphore = asyncio.Semaphore(2)
        remnawave_created = False
        remnawave_key = None
        remnawave_client_id = None
        remnawave_link_value = None

        if remnawave_servers:
            async with remnawave_api(remnawave_servers[0]["api_url"]) as remna:
                logged_in = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
                if not logged_in:
                    logger.error(f"{PANEL_REMNA} Не удалось войти в Remnawave API")
                else:
                    expire_at = datetime.utcfromtimestamp(expiry_timestamp / 1000).isoformat() + "Z"
                    inbound_ids = [s.get("inbound_id") for s in remnawave_servers if s.get("inbound_id")]
                    if inbound_ids:
                        short_uuid = None
                        if remnawave_link and "/" in remnawave_link:
                            short_uuid = remnawave_link.rstrip("/").split("/")[-1]

                        user_data = {
                            "username": email,
                            "trafficLimitStrategy": "NO_RESET",
                            "expireAt": expire_at,
                            "activeInternalSquads": inbound_ids,
                            "uuid": client_id,
                        }

                        if session is not None:
                            try:
                                if panel_tg is not None:
                                    user_data["telegramId"] = panel_tg
                                if panel_email:
                                    user_data["email"] = panel_email
                            except Exception as e:
                                logger.debug(f"{PANEL_REMNA} поля владельца не резолвлены: {e}")

                        if traffic_limit_bytes_value and traffic_limit_bytes_value > 0:
                            user_data["trafficLimitBytes"] = traffic_limit_bytes_value

                        if short_uuid:
                            user_data["shortUuid"] = short_uuid

                        user_data["hwidDeviceLimit"] = device_limit_value

                        if external_squad_uuid:
                            user_data["externalSquadUuid"] = external_squad_uuid

                        logger.debug(f"{PANEL_REMNA} Данные для создания клиента: {user_data}")
                        result = await remna.create_user(user_data)
                        if not result and reuse_existing:
                            existing = await remna.get_user_by_uuid(client_id, username=email)
                            if (
                                existing
                                and str(existing.get("vlessUuid") or existing.get("uuid") or "") == str(client_id)
                                and str(existing.get("username") or "") == email
                            ):
                                restored = await remna.update_user(
                                    uuid=client_id,
                                    lookup_username=email,
                                    expire_at=expire_at,
                                    active_user_inbounds=inbound_ids,
                                    traffic_limit_bytes=traffic_limit_bytes_value,
                                    hwid_device_limit=device_limit_value,
                                    external_squad_uuid=external_squad_uuid or "",
                                    telegram_id=panel_tg,
                                    email=panel_email,
                                )
                                if restored is True:
                                    result = existing
                                    logger.info(
                                        f"{PANEL_REMNA} Восстановлены параметры клиента после повторной операции: {client_id}"
                                    )
                                else:
                                    logger.warning(
                                        f"{PANEL_REMNA} Не удалось восстановить параметры клиента: {client_id}"
                                    )
                            else:
                                logger.warning(
                                    f"{PANEL_REMNA} Существующий клиент не подтверждён для {client_id} / {email}"
                                )
                        if result:
                            remnawave_created = True
                            remnawave_client_id = result.get("vlessUuid") or result.get("uuid")
                            remnawave_link_value = result.get("subscriptionUrl")

                            remnawave_key = None
                            if need_vless_key:
                                try:
                                    remnawave_key = await get_vless_link_for_remnawave_by_username(remna, email, email)
                                except Exception as e:
                                    logger.error(f"{PANEL_REMNA} Ошибка сборки VLESS: {e}")
                            else:
                                crypto_link = await process_extract_cryptolink_from_result(
                                    result=result,
                                    cluster_id=server_id_to_store,
                                    plan=plan,
                                    session=session,
                                    email=email,
                                    tg_id=panel_tg,
                                    need_vless_key=need_vless_key,
                                )
                                if crypto_link:
                                    remnawave_key = crypto_link

                            logger.info(f"{PANEL_REMNA} Пользователь создан: {result}")
                    else:
                        logger.warning(f"{PANEL_REMNA} Нет inbound_id у серверов")

        final_client_id = remnawave_client_id or client_id
        xui_confirmed = False

        logger.debug(f"{PANEL_XUI} 3x-ui servers для кластера {cluster_id}: {[s['server_name'] for s in xui_servers]}")

        if xui_servers:
            if SUPERNODE:
                for server_info in xui_servers:
                    confirmed = await create_client_on_server(
                        server_info,
                        tg_id,
                        final_client_id,
                        email,
                        expiry_timestamp,
                        semaphore,
                        panel_tg_id=panel_tg,
                        is_trial=is_trial,
                        total_traffic_limit_bytes=traffic_limit_bytes_value,
                        device_limit_value=device_limit_value,
                        reuse_existing=reuse_existing,
                    )
                    xui_confirmed = xui_confirmed or confirmed is True
            else:
                tasks = [
                    create_client_on_server(
                        server,
                        tg_id,
                        final_client_id,
                        email,
                        expiry_timestamp,
                        semaphore,
                        panel_tg_id=panel_tg,
                        is_trial=is_trial,
                        total_traffic_limit_bytes=traffic_limit_bytes_value,
                        device_limit_value=device_limit_value,
                        reuse_existing=reuse_existing,
                    )
                    for server in xui_servers
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                xui_confirmed = any(result is True for result in results)

        if not ((remnawave_created and remnawave_client_id) or xui_confirmed):
            raise ValueError("Создание ключа не подтверждено ни на одном сервере")

        cluster_all = enabled_servers
        subgroup_code = subgroup_title if subgroup_title else None

        link_ref = await subscription_owner_ref(session, tg_id)

        public_link = await make_aggregated_link(
            session=session,
            cluster_all=cluster_all,
            cluster_id=server_id_to_store,
            email=email,
            client_id=final_client_id,
            tg_id=link_ref,
            subgroup_code=subgroup_code,
            remna_link_override=remnawave_key or remnawave_link_value,
            plan=plan,
        )

        if not public_link:
            public_link = f"{PUBLIC_LINK}{email}/{link_ref}"

        if (remnawave_created and remnawave_client_id) or xui_confirmed:
            await store_key(
                session=session,
                legacy_user_ref=tg_id,
                client_id=final_client_id,
                email=email,
                expiry_time=expiry_timestamp,
                key=public_link,
                server_id=server_id_to_store,
                remnawave_link=remnawave_link_value if remnawave_created else None,
                tariff_id=plan,
                selected_device_limit=selected_device_limit,
                selected_traffic_limit=selected_traffic_limit_gb,
                current_device_limit=current_device_limit,
                current_traffic_limit=current_traffic_limit_gb,
                selected_price_rub=selected_price_rub,
            )
            await mark_trial_started_if_eligible(session, tg_id)
            try:
                from database.web_notifications import notify_web

                await notify_web(
                    session,
                    user_ref=tg_id,
                    type="key_created",
                    template_vars={"email": email},
                    data={"email": email, "client_id": client_id},
                )
            except Exception as e:
                logger.warning("[KeyCreate] Ошибка web-уведомления о создании ключа tg_id={}: {}", tg_id, e)

    except Exception as e:
        logger.error(f"Ошибка при создании ключа: {e}")
        raise e


async def create_client_on_server(
    server_info: dict,
    tg_id: int,
    client_id: str,
    email: str,
    expiry_timestamp: int,
    semaphore: asyncio.Semaphore,
    plan: int | None = None,
    session: AsyncSession | None = None,
    is_trial: bool = False,
    total_traffic_limit_bytes: int = 0,
    device_limit_value: int = 0,
    reuse_existing: bool = False,
    panel_tg_id: int | None = None,
) -> bool:
    """Создаёт клиента и подтверждает его наличие на сервере."""

    logger.debug(
        f"{PANEL_XUI} [Client] Вход в create_client_on_server: "
        f"сервер={server_info.get('server_name')}, план={plan}, is_trial={is_trial}"
    )

    async with semaphore:
        xui = await get_xui_instance(server_info["api_url"])
        inbound_id = server_info.get("inbound_id")
        server_name = server_info.get("server_name", "unknown")

        if not inbound_id:
            logger.warning(f"{PANEL_XUI} [Client] INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
            return False

        panel_tg = None if isinstance(tg_id, UserId) else tg_id
        if session is not None:
            panel_tg, _ = await panel_identity_fields(session, tg_id)
        elif panel_tg_id is not None:
            panel_tg = panel_tg_id

        if SUPERNODE:
            unique_email = f"{email}_{server_name.lower()}"
            sub_id = email
        else:
            unique_email = email
            sub_id = unique_email

        if plan is not None and (total_traffic_limit_bytes == 0 or device_limit_value == 0):
            tariff = await get_tariff_by_id(session, plan)
            logger.debug(f"{PANEL_XUI} [Tariff Debug] Получен тариф: {tariff}")
            if not tariff:
                raise ValueError(f"{PANEL_XUI} Тариф с id={plan} не найден.")

            if total_traffic_limit_bytes == 0:
                raw_limit = tariff.get("traffic_limit")
                base_gb = int(raw_limit) if raw_limit else 0
                total_traffic_limit_bytes = base_gb * 1024 * 1024 * 1024

            if device_limit_value == 0:
                raw_device_limit = tariff.get("device_limit")
                device_limit_value = int(raw_device_limit) if raw_device_limit is not None else 0

        confirmed = False
        try:
            logger.debug(
                f"{PANEL_XUI} [Client] Вызов add_client: email={email}, client_id={client_id}, "
                f"bytes={total_traffic_limit_bytes}, Devices={device_limit_value}"
            )
            traffic_limit_bytes = total_traffic_limit_bytes
            result = await add_client(
                xui,
                ClientConfig(
                    client_id=client_id,
                    email=unique_email,
                    tg_id=panel_tg if panel_tg and int(panel_tg) > 0 else "",
                    limit_ip=device_limit_value,
                    total_gb=traffic_limit_bytes,
                    expiry_time=expiry_timestamp,
                    enable=True,
                    inbound_id=int(inbound_id),
                    sub_id=sub_id,
                ),
            )
            confirmed = bool(
                result and result.get("status") == "success" and str(result.get("client_id") or "") == client_id
            )
            if reuse_existing and result and result.get("status") == "duplicate":
                client, _ = await _client_snapshot(xui, int(inbound_id), unique_email, client_id)
                client.update(
                    expiryTime=expiry_timestamp,
                    subId=sub_id,
                    totalGB=traffic_limit_bytes,
                    enable=True,
                    limitIp=device_limit_value,
                    tgId=int(panel_tg) if panel_tg and int(panel_tg) > 0 else 0,
                )
                await _update_client(xui, unique_email, client)
                confirmed = True
            if confirmed:
                logger.info(f"{PANEL_XUI} [Client] Клиент успешно добавлен на сервер {server_name}")
            else:
                logger.warning(f"{PANEL_XUI} [Client] Создание клиента не подтверждено на {server_name}: {client_id}")
        except Exception as e:
            logger.error(f"{PANEL_XUI} [Client Error] Не удалось создать клиента на {server_name}: {e}")

        if SUPERNODE:
            await asyncio.sleep(0.7)
        return confirmed
