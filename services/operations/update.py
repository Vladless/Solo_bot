import asyncio

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from database import filter_cluster_by_subgroup, filter_cluster_by_tariff, get_servers, get_tariff_by_id, store_key
from database.access.resolution import (
    TelegramId,
    UserId,
    panel_identity_fields,
    subscription_owner_ref,
    user_id_from_legacy_ref,
)
from database.keys import delete_key_by_user_and_email, get_key_by_user_and_email, resolve_current_key_owner
from database.models import Key
from database.tariffs import get_active_tariff_by_id
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
)
from panels.remnawave_runtime import invalidate_remnawave_profile, with_remnawave_api
from services.clusters import ALLOWED_GROUP_CODES, select_cluster
from services.subscription_links import preserve_saved_public_link, saved_public_subscription_tail
from services.tariffs.tariff_display import GB, get_effective_limits_for_key
from settings.config import PUBLIC_LINK, SUPERNODE

from .aggregated_links import make_aggregated_link
from .creation import create_client_on_server
from .deletion import delete_on_3xui, delete_on_remnawave


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
    billing_user_id: int | None = None,
):
    try:
        owner = UserId(billing_user_id) if billing_user_id is not None else tg_id
        panel_tg, panel_email = await panel_identity_fields(session, owner)
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
            raise ValueError(f"Нет серверов для перевыпуска в кластере {cluster_id}")

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
            raise ValueError(f"Нет серверов для перевыпуска в кластере {cluster_id}")

        expire_iso = datetime.utcfromtimestamp(expiry_time / 1000).replace(tzinfo=timezone.utc).isoformat()

        remnawave_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"]
        xui_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "3x-ui"]
        if len(remnawave_servers) + len(xui_servers) != len(cluster):
            raise ValueError("Неизвестный тип панели при перевыпуске подписки")

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
                if panel_tg is not None:
                    user_data["telegramId"] = panel_tg
                if panel_email:
                    user_data["email"] = panel_email
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
                if (
                    not isinstance(created, dict)
                    or str(created.get("vlessUuid") or created.get("uuid") or "") != client_id
                    or str(created.get("username") or "") != email
                ):
                    return None
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
            if (
                isinstance(remna_result, dict)
                and str(remna_result.get("vlessUuid") or remna_result.get("uuid") or "") == client_id
                and str(remna_result.get("username") or "") == email
            ):
                remnawave_client_id = remna_result.get("vlessUuid") or remna_result.get("uuid")
                remnawave_link_value = remna_result.get("subscriptionUrl")
                try:
                    await invalidate_remnawave_profile(
                        session,
                        str(remnawave_servers[0].get("server_name") or cluster_id),
                        str(remnawave_client_id or client_id),
                        fallback_any=True,
                    )
                except Exception as e:
                    logger.warning(f"{PANEL_REMNA} Не удалось сбросить кэш профиля {client_id}: {e}")
                logger.info(f"{PANEL_REMNA} Клиент заново создан, uuid={remnawave_client_id}")
            else:
                logger.error(f"{PANEL_REMNA} Не удалось авторизоваться/создать клиента")

        remna_confirmed = bool(remnawave_client_id)
        if not remnawave_client_id:
            logger.warning(f"{PANEL_REMNA} client_id не получен, используем исходный {client_id}")
            remnawave_client_id = client_id

        tasks = []
        semaphore = asyncio.Semaphore(2)
        for server_info in xui_servers:
            tasks.append(
                create_client_on_server(
                    server_info,
                    owner,
                    remnawave_client_id,
                    email,
                    expiry_time,
                    semaphore,
                    total_traffic_limit_bytes=int(traffic_limit * 1024**3) if traffic_limit is not None else 0,
                    device_limit_value=device_limit if device_limit is not None else 0,
                    panel_tg_id=panel_tg,
                    reset_traffic=True,
                )
            )

        xui_confirmed = not xui_servers
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            xui_confirmed = all(result is True for result in results)
        if (remnawave_servers and not remna_confirmed) or not xui_confirmed:
            raise ValueError("Перевыпуск не подтверждён на всех серверах")

        logger.info(f"[Update] Ключ {remnawave_client_id} обновлён на серверах подгруппы в {cluster_id}")
        return remnawave_client_id, remnawave_link_value

    except Exception as e:
        logger.error(f"[Update Error] Ошибка при обновлении ключа {client_id} на {cluster_id}: {e}")
        raise


async def update_subscription(
    user_id: int,
    email: str,
    session: AsyncSession,
    cluster_override: str = None,
    country_override: str = None,
    remnawave_link: str = None,
    *,
    tg_id: int | None = None,
) -> None:
    """Пересоздаёт подписку клиента."""
    ref = UserId(user_id) if user_id is not None else TelegramId(tg_id)
    uid = await user_id_from_legacy_ref(session, ref)
    if uid is None:
        raise ValueError(f"The key {email} does not exist in database")
    user_id = uid
    record: Key | None = await get_key_by_user_and_email(session, uid, email)
    if not record:
        raise ValueError(f"The key {email} does not exist in database")
    if record.is_frozen:
        raise ValueError("Сначала разморозьте подписку для перевыпуска")

    expiry_time = record.expiry_time
    client_id = record.client_id
    old_cluster_id = record.server_id
    old_key_link = record.key
    tariff_id = record.tariff_id
    alias = record.alias
    remnawave_link = remnawave_link or record.remnawave_link
    owner_ref = saved_public_subscription_tail(old_key_link, email, PUBLIC_LINK)
    if owner_ref is None:
        owner_ref = await subscription_owner_ref(session, user_id)
    public_link = f"{PUBLIC_LINK.rstrip('/')}/{email}/{owner_ref}"

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

    user_id = await resolve_current_key_owner(session, client_id, email)
    if user_id is None:
        raise ValueError(f"Исходная подписка {email} удалена или её владелец неоднозначен")
    await release_session_early(session)

    def log_reissue_failure(reason: str) -> None:
        logger.warning(f"[Update] Перевыпуск {email} не завершён ({reason}), прежняя запись сохранена")

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
    old_servers = servers.get(old_cluster_id) or [
        server
        for cluster in servers.values()
        for server in cluster
        if str(server.get("server_name") or "").lower() == old_cluster_id.lower()
    ]
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
        log_reissue_failure(f"нет серверов после фильтрации в {new_cluster_id}")
        raise ValueError(f"Нет серверов для перевыпуска в кластере {new_cluster_id}")

    if tariff:
        gc = (getattr(tariff, "group_code", None) or "").lower()
        if gc in ALLOWED_GROUP_CODES:
            bound_servers = [s for s in cluster_servers if gc in (s.get("special_groups") or [])]
            if bound_servers:
                cluster_servers = bound_servers
            else:
                logger.info(f"[Update] Нет серверов со спецгруппой '{gc}' в {new_cluster_id}")

    if not cluster_servers:
        log_reissue_failure(f"нет серверов после фильтрации по спецгруппам в {new_cluster_id}")
        raise ValueError(f"Нет серверов для перевыпуска в кластере {new_cluster_id}")

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

    user_id = await resolve_current_key_owner(session, client_id, email)
    if user_id is None:
        raise ValueError(f"Исходная подписка {email} удалена или её владелец неоднозначен")

    try:
        new_client_id, remnawave_link_value = await update_key_on_cluster(
            tg_id=owner_ref,
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
            billing_user_id=user_id,
        )

        aggregated = await make_aggregated_link(
            session=session,
            cluster_all=cluster_servers,
            cluster_id=new_cluster_id,
            email=email,
            client_id=new_client_id,
            tg_id=owner_ref,
            subgroup_code=subgroup_code,
            remna_link_override=None,
            plan=tariff_id,
        )
    except Exception as e:
        log_reissue_failure(f"ошибка пересоздания: {e}")
        raise

    final_key_link = preserve_saved_public_link(aggregated or public_link, old_key_link, email, PUBLIC_LINK)

    target_xui = {
        (
            str(server.get("api_url") or "").rstrip("/"),
            str(server.get("inbound_id") or ""),
            str(server.get("server_name") or "").lower() if SUPERNODE else "",
        )
        for server in cluster_servers
        if server.get("panel_type", "3x-ui").lower() == "3x-ui"
    }
    target_remna = {
        str(server.get("api_url") or "").rstrip("/")
        for server in cluster_servers
        if server.get("panel_type", "3x-ui").lower() == "remnawave"
    }
    old_xui = [
        server
        for server in old_servers
        if server.get("panel_type", "3x-ui").lower() == "3x-ui"
        and (
            str(server.get("api_url") or "").rstrip("/"),
            str(server.get("inbound_id") or ""),
            str(server.get("server_name") or "").lower() if SUPERNODE else "",
        )
        not in target_xui
    ]
    old_remna = [
        server
        for server in old_servers
        if server.get("panel_type", "3x-ui").lower() == "remnawave"
        and str(server.get("api_url") or "").rstrip("/") not in target_remna
    ]
    cleanup = await asyncio.gather(
        delete_on_3xui(old_xui, email, client_id),
        delete_on_remnawave(old_remna, client_id, username=email, subscription_url=remnawave_link),
        return_exceptions=True,
    )
    if any(result is not True for result in cleanup):
        logger.warning(f"[Update] Не удалось удалить все прежние подключения {email} после перевыпуска")

    user_id = await resolve_current_key_owner(session, client_id, email)
    if user_id is None:
        raise ValueError(f"Исходная подписка {email} удалена или её владелец неоднозначен")
    if new_client_id != client_id:
        await delete_key_by_user_and_email(session, user_id, email)
    await store_key(
        session=session,
        legacy_user_ref=user_id,
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
