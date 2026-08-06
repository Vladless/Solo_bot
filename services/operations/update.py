import asyncio

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from database import filter_cluster_by_subgroup, filter_cluster_by_tariff, get_servers, get_tariff_by_id, store_key
from database.access.resolution import resolve_user_optional
from database.keys import delete_key_by_user_and_email, get_key_by_user_and_email
from database.models import Key
from database.tariffs import get_active_tariff_by_id
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import ClientConfig, add_client, get_xui_instance
from panels.remnawave_runtime import invalidate_remnawave_profile, with_remnawave_api
from services.clusters import ALLOWED_GROUP_CODES, select_cluster
from services.tariffs.tariff_display import GB, get_effective_limits_for_key
from settings.config import PUBLIC_LINK, SUPERNODE

from .aggregated_links import make_aggregated_link
from .deletion import delete_key_from_cluster


async def update_key_on_cluster(
    tg_id: int,
    client_id: str,
    email: str,
    expiry_time: int,
    cluster_id: str,
    session: AsyncSession,
    traffic_limit: int = None,
    device_limit: int = None,
    remnawave_link: str = None,
    subgroup_code: str | None = None,
    tariff_id: int | None = None,
    external_squad_uuid: str | None = None,
):
    try:
        servers = await get_servers(session)
        cluster = servers.get(cluster_id)

        if not cluster:
            found_servers = []
            for _key, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        found_servers.append(server_info)
            if found_servers:
                cluster = found_servers
            else:
                raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")

        if tariff_id is not None:
            filtered = await filter_cluster_by_tariff(session, cluster, tariff_id, cluster_id)
            if filtered is not cluster:
                cluster = filtered
            elif subgroup_code:
                cluster = await filter_cluster_by_subgroup(
                    session, cluster, subgroup_code, cluster_id, tariff_id=tariff_id
                )
        elif subgroup_code:
            cluster = await filter_cluster_by_subgroup(session, cluster, subgroup_code, cluster_id, tariff_id=tariff_id)

        if not cluster:
            logger.warning(f"[Update] Нет серверов после фильтрации по привязкам в кластере {cluster_id}")
            return client_id, remnawave_link

        if tariff_id is not None:
            tariff = await get_tariff_by_id(session, tariff_id)
            if tariff:
                gc = (tariff.get("group_code") or "").lower()
                if gc in ALLOWED_GROUP_CODES:
                    bound_servers = [s for s in cluster if gc in (s.get("special_groups") or [])]
                    if bound_servers:
                        cluster = bound_servers
                    else:
                        logger.info(f"[Update] Нет серверов со спецгруппой '{gc}' в {cluster_id}")

        if not cluster:
            logger.warning(f"[Update] Нет серверов после фильтрации по спецгруппам в кластере {cluster_id}")
            return client_id, remnawave_link

        expire_iso = datetime.utcfromtimestamp(expiry_time / 1000).replace(tzinfo=timezone.utc).isoformat()

        remnawave_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"]
        xui_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "3x-ui"]

        remnawave_client_id = None
        remnawave_link_value = None

        if remnawave_servers:
            inbound_ids = [s["inbound_id"] for s in remnawave_servers if s.get("inbound_id")]
            group_code = remnawave_servers[0].get("tariff_group")
            if not group_code:
                raise ValueError("У Remnawave-сервера отсутствует tariff_group")

            short_uuid = None
            if remnawave_link and "/" in remnawave_link:
                short_uuid = remnawave_link.rstrip("/").split("/")[-1]
                logger.debug(f"{PANEL_REMNA} Извлечен short_uuid: {short_uuid}")

            user_data = {
                "username": email,
                "trafficLimitStrategy": "NO_RESET",
                "expireAt": expire_iso,
                "activeInternalSquads": inbound_ids,
                "uuid": client_id,
            }

            try:
                from database.access.resolution import panel_identity_fields

                _panel_tg, _panel_email = await panel_identity_fields(session, tg_id)
                if _panel_tg is not None:
                    user_data["telegramId"] = _panel_tg
                if _panel_email:
                    user_data["email"] = _panel_email
            except Exception as e:
                logger.debug(f"{PANEL_REMNA} поля владельца не резолвлены: {e}")

            if external_squad_uuid:
                user_data["externalSquadUuid"] = external_squad_uuid

            if traffic_limit is not None:
                user_data["trafficLimitBytes"] = traffic_limit * 1024**3
            if device_limit is not None:
                user_data["hwidDeviceLimit"] = device_limit
            if short_uuid:
                user_data["shortUuid"] = short_uuid
                logger.debug(f"{PANEL_REMNA} Добавлен short_uuid: {short_uuid}")

            async def _recreate(api):
                await api.delete_user(client_id, username=email)
                created = await api.create_user(user_data)
                try:
                    reset_uuid = str((created or {}).get("vlessUuid") or (created or {}).get("uuid") or client_id)
                    devices = await api.get_user_hwid_devices(reset_uuid, username=email) or []
                    removed = 0
                    for d in devices:
                        hwid = d.get("hwid") if isinstance(d, dict) else None
                        if hwid and await api.delete_user_hwid_device(reset_uuid, hwid, username=email):
                            removed += 1
                    if removed:
                        logger.info(
                            f"{PANEL_REMNA} HWID сброшены при перевыпуске: {removed} устройств, uuid={reset_uuid}"
                        )
                except Exception as hwid_err:
                    logger.warning(f"{PANEL_REMNA} не удалось сбросить HWID при перевыпуске: {hwid_err}")
                return created

            remna_result = await with_remnawave_api(
                session,
                str(remnawave_servers[0].get("server_name") or cluster_id),
                _recreate,
                fallback_any=True,
                timeout_sec=12.0,
            )
            if remna_result:
                remnawave_client_id = remna_result.get("vlessUuid") or remna_result.get("uuid")
                remnawave_link_value = remna_result.get("subscriptionUrl")
                await invalidate_remnawave_profile(
                    session,
                    str(remnawave_servers[0].get("server_name") or cluster_id),
                    str(remnawave_client_id or client_id),
                    fallback_any=True,
                )
                logger.info(f"{PANEL_REMNA} Клиент заново создан, uuid={remnawave_client_id}")
            else:
                logger.error(f"{PANEL_REMNA} Не удалось авторизоваться/создать клиента")

        if not remnawave_client_id:
            logger.warning(f"{PANEL_REMNA} client_id не получен, используем исходный {client_id}")
            remnawave_client_id = client_id

        tasks = []
        for server_info in xui_servers:
            server_name = server_info.get("server_name", "unknown")
            inbound_id = server_info.get("inbound_id")

            if not inbound_id:
                logger.warning(f"{PANEL_XUI} INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                continue

            try:
                xui = await get_xui_instance(server_info["api_url"])
            except Exception as e:
                logger.warning(f"{PANEL_XUI} [{server_name}] недоступна панель 3x-ui при перевыпуске: {e}")
                continue

            sub_id = email
            unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email

            group_code = server_info.get("tariff_group")
            if not group_code:
                raise ValueError(f"У сервера {server_name} отсутствует tariff_group")

            total_gb_bytes = int(traffic_limit * 1024**3) if traffic_limit is not None else 0
            device_limit_value = device_limit if device_limit is not None else 0

            config = ClientConfig(
                client_id=remnawave_client_id,
                email=unique_email,
                tg_id=tg_id,
                limit_ip=device_limit_value,
                total_gb=total_gb_bytes,
                expiry_time=expiry_time,
                enable=True,
                inbound_id=int(inbound_id),
                sub_id=sub_id,
            )

            tasks.append(add_client(xui, config))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"[Update] Ключ {remnawave_client_id} обновлён на серверах подгруппы в {cluster_id}")
        return remnawave_client_id, remnawave_link_value

    except Exception as e:
        logger.error(f"[Update Error] Ошибка при обновлении ключа {client_id} на {cluster_id}: {e}")
        raise


async def update_subscription(
    tg_id: int,
    email: str,
    session: AsyncSession,
    cluster_override: str = None,
    country_override: str = None,
    remnawave_link: str = None,
) -> None:
    u = await resolve_user_optional(session, tg_id)
    if u is None:
        raise ValueError(f"The key {email} does not exist in database")
    uid = u.id
    record: Key | None = await get_key_by_user_and_email(session, uid, email)
    if not record:
        raise ValueError(f"The key {email} does not exist in database")

    expiry_time = record.expiry_time
    client_id = record.client_id
    old_cluster_id = record.server_id
    old_key_link = record.key
    tariff_id = record.tariff_id
    alias = record.alias
    remnawave_link = remnawave_link or record.remnawave_link
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

    selected_device_limit = getattr(record, "selected_device_limit", None)
    selected_traffic_limit = getattr(record, "selected_traffic_limit", None)
    selected_price_rub = getattr(record, "selected_price_rub", None)
    current_device_limit_db = getattr(record, "current_device_limit", None)
    current_traffic_limit_db = getattr(record, "current_traffic_limit", None)

    tariff = None
    subgroup_code = getattr(record, "subgroup_code", None)
    external_squad_uuid = None

    if tariff_id:
        tariff = await get_active_tariff_by_id(session, int(tariff_id))
        if tariff is None:
            logger.warning(f"[LOG] update_subscription: тариф с id={tariff_id} не найден!")
        else:
            if not subgroup_code:
                subgroup_code = getattr(tariff, "subgroup_code", None) or getattr(tariff, "subgroup_title", None)
            external_squad_uuid = tariff.external_squad
    else:
        logger.warning("[LOG] update_subscription: tariff_id отсутствует!")

    from middlewares.session import release_session_early

    await release_session_early(session)
    await delete_key_from_cluster(old_cluster_id, email, client_id, session=session)
    await delete_key_by_user_and_email(session, uid, email)

    async def restore_previous_key(reason: str) -> None:
        logger.warning(f"[Update] Перевыпуск {email} не завершён ({reason}), восстанавливаем прежнюю запись")
        await store_key(
            session=session,
            legacy_user_ref=tg_id,
            client_id=client_id,
            email=email,
            expiry_time=expiry_time,
            key=old_key_link,
            remnawave_link=remnawave_link,
            server_id=old_cluster_id,
            tariff_id=tariff_id,
            alias=alias,
            selected_device_limit=selected_device_limit,
            selected_traffic_limit=selected_traffic_limit,
            selected_price_rub=selected_price_rub,
            current_device_limit=current_device_limit_db,
            current_traffic_limit=current_traffic_limit_db,
        )

    if country_override or cluster_override:
        new_cluster_id = country_override or cluster_override
    else:
        try:
            result = await select_cluster(session)
            new_cluster_id = result.cluster_name
        except ValueError:
            logger.warning("[Update] Нет доступных кластеров, оставляем на старом")
            new_cluster_id = old_cluster_id

    servers = await get_servers(session)
    cluster_servers = servers.get(new_cluster_id)

    if cluster_servers is None:
        for server_list in servers.values():
            for server_info in server_list:
                if server_info.get("server_name", "").lower() == new_cluster_id.lower():
                    cluster_servers = [server_info]
                    break
            if cluster_servers:
                break
        else:
            cluster_servers = []

    if tariff_id is not None:
        filtered = await filter_cluster_by_tariff(session, cluster_servers, tariff_id, new_cluster_id)
        if filtered is not cluster_servers:
            cluster_servers = filtered
        elif subgroup_code:
            cluster_servers = await filter_cluster_by_subgroup(
                session, cluster_servers, subgroup_code, new_cluster_id, tariff_id=tariff_id
            )
    elif subgroup_code:
        cluster_servers = await filter_cluster_by_subgroup(
            session, cluster_servers, subgroup_code, new_cluster_id, tariff_id=tariff_id
        )

    if not cluster_servers:
        await restore_previous_key(f"нет серверов после фильтрации в {new_cluster_id}")
        return

    if tariff:
        gc = (getattr(tariff, "group_code", None) or "").lower()
        if gc in ALLOWED_GROUP_CODES:
            bound_servers = [s for s in cluster_servers if gc in (s.get("special_groups") or [])]
            if bound_servers:
                cluster_servers = bound_servers
            else:
                logger.info(f"[Update] Нет серверов со спецгруппой '{gc}' в {new_cluster_id}")

    if not cluster_servers:
        await restore_previous_key(f"нет серверов после фильтрации по спецгруппам в {new_cluster_id}")
        return

    traffic_limit_gb = None
    device_limit = 0

    if tariff and tariff_id:
        device_limit_effective, traffic_limit_bytes_effective = await get_effective_limits_for_key(
            session=session,
            tariff_id=int(tariff_id),
            selected_device_limit=int(selected_device_limit) if selected_device_limit is not None else None,
            selected_traffic_gb=int(selected_traffic_limit) if selected_traffic_limit is not None else None,
        )
        device_limit = int(device_limit_effective or 0)
        traffic_limit_gb = int(traffic_limit_bytes_effective / GB) if traffic_limit_bytes_effective else None
    elif tariff:
        traffic_limit_gb = int(tariff.traffic_limit) if tariff.traffic_limit is not None else None
        device_limit = int(tariff.device_limit) if tariff.device_limit is not None else 0

    if current_device_limit_db is not None:
        device_limit = int(current_device_limit_db)
    if current_traffic_limit_db is not None:
        traffic_limit_gb = int(current_traffic_limit_db)

    try:
        new_client_id, remnawave_link_value = await update_key_on_cluster(
            tg_id=tg_id,
            client_id=client_id,
            email=email,
            expiry_time=expiry_time,
            cluster_id=new_cluster_id,
            session=session,
            traffic_limit=traffic_limit_gb,
            device_limit=device_limit,
            remnawave_link=remnawave_link,
            subgroup_code=subgroup_code,
            tariff_id=tariff_id,
            external_squad_uuid=external_squad_uuid,
        )

        aggregated = await make_aggregated_link(
            session=session,
            cluster_all=cluster_servers,
            cluster_id=new_cluster_id,
            email=email,
            client_id=new_client_id,
            tg_id=tg_id,
            subgroup_code=subgroup_code,
            remna_link_override=None,
            plan=tariff_id,
        )
    except Exception as e:
        await restore_previous_key(f"ошибка пересоздания: {e}")
        raise

    final_key_link = aggregated or public_link

    await store_key(
        session=session,
        legacy_user_ref=tg_id,
        client_id=new_client_id,
        email=email,
        expiry_time=expiry_time,
        key=final_key_link,
        remnawave_link=remnawave_link_value or remnawave_link,
        server_id=new_cluster_id,
        tariff_id=tariff_id,
        alias=alias,
        selected_device_limit=selected_device_limit,
        selected_traffic_limit=selected_traffic_limit,
        selected_price_rub=selected_price_rub,
        current_device_limit=current_device_limit_db,
        current_traffic_limit=current_traffic_limit_db,
    )
