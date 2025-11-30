import asyncio

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from config import PUBLIC_LINK, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from database import get_servers, get_tariff_by_id, store_key
from database.models import User
from handlers.utils import ALLOWED_GROUP_CODES, check_server_key_limit
from hooks.processors import process_extract_cryptolink_from_result
from logger import (
    CLOGGER as logger,
    PANEL_REMNA,
    PANEL_XUI,
)
from panels._3xui import ClientConfig, add_client, get_xui_instance
from panels.remnawave import RemnawaveAPI, get_vless_link_for_remnawave_by_username

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
):
    try:
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
            logger.warning(f"[Key Creation] Нет доступных серверов в кластере {cluster_id}")
            return

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

        if subgroup_title:
            subgroup_servers = [s for s in enabled_servers if subgroup_title in s.get("tariff_subgroups", [])]
            if subgroup_servers:
                enabled_servers = subgroup_servers
            else:
                logger.warning(
                    f"[Key Creation] В кластере {cluster_id} не найдено серверов для подгруппы '{subgroup_title}'. "
                    f"Использую весь кластер."
                )

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
            logger.warning(f"[Key Creation] Нет серверов с доступным лимитом в кластере {cluster_id}")
            return

        semaphore = asyncio.Semaphore(2)
        remnawave_created = False
        remnawave_key = None
        remnawave_client_id = None
        remnawave_link_value = None

        if remnawave_servers:
            remna = RemnawaveAPI(remnawave_servers[0]["api_url"])
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
                        "telegramId": tg_id,
                        "activeInternalSquads": inbound_ids,
                        "uuid": client_id,
                    }

                    if traffic_limit_bytes_value and traffic_limit_bytes_value > 0:
                        user_data["trafficLimitBytes"] = traffic_limit_bytes_value

                    if short_uuid:
                        user_data["shortUuid"] = short_uuid

                    user_data["hwidDeviceLimit"] = device_limit_value

                    if external_squad_uuid:
                        user_data["externalSquadUuid"] = external_squad_uuid

                    logger.debug(f"{PANEL_REMNA} Данные для создания клиента: {user_data}")
                    result = await remna.create_user(user_data)
                    if result:
                        remnawave_created = True
                        remnawave_client_id = result.get("uuid")
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
                                tg_id=tg_id,
                                need_vless_key=need_vless_key,
                            )
                            if crypto_link:
                                remnawave_key = crypto_link

                        logger.info(f"{PANEL_REMNA} Пользователь создан: {result}")
                else:
                    logger.warning(f"{PANEL_REMNA} Нет inbound_id у серверов")

        final_client_id = remnawave_client_id or client_id

        logger.debug(f"{PANEL_XUI} 3x-ui servers для кластера {cluster_id}: {[s['server_name'] for s in xui_servers]}")

        if xui_servers:
            if SUPERNODE:
                for server_info in xui_servers:
                    await create_client_on_server(
                        server_info,
                        tg_id,
                        final_client_id,
                        email,
                        expiry_timestamp,
                        semaphore,
                        plan=plan,
                        session=session,
                        is_trial=is_trial,
                        total_traffic_limit_bytes=traffic_limit_bytes_value,
                        device_limit_value=device_limit_value,
                    )
            else:
                tasks = [
                    create_client_on_server(
                        server,
                        tg_id,
                        final_client_id,
                        email,
                        expiry_timestamp,
                        semaphore,
                        plan=plan,
                        session=session,
                        is_trial=is_trial,
                        total_traffic_limit_bytes=traffic_limit_bytes_value,
                        device_limit_value=device_limit_value,
                    )
                    for server in xui_servers
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

        cluster_all = enabled_servers
        subgroup_code = subgroup_title if subgroup_title else None

        public_link = await make_aggregated_link(
            session=session,
            cluster_all=cluster_all,
            cluster_id=server_id_to_store,
            email=email,
            client_id=final_client_id,
            tg_id=tg_id,
            subgroup_code=subgroup_code,
            remna_link_override=remnawave_key,
            plan=plan,
        )

        if not public_link:
            public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

        if (remnawave_created and remnawave_client_id) or xui_servers:
            await store_key(
                session=session,
                tg_id=tg_id,
                client_id=final_client_id,
                email=email,
                expiry_time=expiry_timestamp,
                key=public_link,
                server_id=server_id_to_store,
                remnawave_link=remnawave_link_value if remnawave_created else None,
                tariff_id=plan,
            )
            await session.execute(update(User).where(User.tg_id == tg_id, User.trial.in_([0, -1])).values(trial=1))
            await session.commit()

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
):
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
            return

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

        try:
            logger.debug(
                f"{PANEL_XUI} [Client] Вызов add_client: email={email}, client_id={client_id}, "
                f"bytes={total_traffic_limit_bytes}, Devices={device_limit_value}"
            )
            traffic_limit_bytes = total_traffic_limit_bytes
            await add_client(
                xui,
                ClientConfig(
                    client_id=client_id,
                    email=unique_email,
                    tg_id=tg_id,
                    limit_ip=device_limit_value,
                    total_gb=traffic_limit_bytes,
                    expiry_time=expiry_timestamp,
                    enable=True,
                    flow="xtls-rprx-vision",
                    inbound_id=int(inbound_id),
                    sub_id=sub_id,
                ),
            )
            logger.info(f"{PANEL_XUI} [Client] Клиент успешно добавлен на сервер {server_name}")
        except Exception as e:
            logger.error(f"{PANEL_XUI} [Client Error] Не удалось создать клиента на {server_name}: {e}")

        if SUPERNODE:
            await asyncio.sleep(0.7)
