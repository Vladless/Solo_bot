import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import PUBLIC_LINK, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE, TRIAL_CONFIG
from database import delete_notification, get_servers, get_tariff_by_id, store_key
from database.models import Key, Server, Tariff
from handlers.utils import check_server_key_limit, get_least_loaded_cluster
from logger import logger
from panels.remnawave import RemnawaveAPI
from panels.three_xui import (
    ClientConfig,
    add_client,
    delete_client,
    extend_client_key,
    get_client_traffic,
    get_xui_instance,
    toggle_client,
)


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
        servers = await get_servers(session, include_enabled=True)
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
                raise ValueError(
                    f"Кластер или сервер с ID/именем {cluster_id} не найден."
                )

        enabled_servers = [s for s in cluster if s.get("enabled", True)]
        if not enabled_servers:
            logger.warning(
                f"[Key Creation] Нет доступных серверов в кластере {cluster_id}"
            )
            return

        if plan is not None and traffic_limit_bytes is None:
            tariff = await get_tariff_by_id(session, plan)
            if not tariff:
                raise ValueError(f"Тариф с id={plan} не найден.")
            traffic_limit_bytes = (
                int(tariff["traffic_limit"]) if tariff["traffic_limit"] else None
            )
            if hwid_limit is None and tariff.get("device_limit") is not None:
                hwid_limit = int(tariff["device_limit"])

        remnawave_servers = [
            s
            for s in enabled_servers
            if s.get("panel_type", "3x-ui").lower() == "remnawave"
            and await check_server_key_limit(s, session)
        ]
        xui_servers = [
            s
            for s in enabled_servers
            if s.get("panel_type", "3x-ui").lower() == "3x-ui"
            and await check_server_key_limit(s, session)
        ]

        if not remnawave_servers and not xui_servers:
            logger.warning(
                f"[Key Creation] Нет серверов с доступным лимитом в кластере {cluster_id}"
            )
            return

        semaphore = asyncio.Semaphore(2)
        remnawave_created = False
        remnawave_key = None
        remnawave_client_id = None

        if remnawave_servers:
            remna = RemnawaveAPI(remnawave_servers[0]["api_url"])
            logged_in = await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD)
            if not logged_in:
                logger.error("Не удалось войти в Remnawave API")
            else:
                expire_at = (
                    datetime.utcfromtimestamp(expiry_timestamp / 1000).isoformat() + "Z"
                )
                inbound_ids = [
                    s.get("inbound_id")
                    for s in remnawave_servers
                    if s.get("inbound_id")
                ]

                if not inbound_ids:
                    logger.warning("Нет inbound_id у серверов Remnawave")
                else:
                    short_uuid = None
                    if remnawave_link and "/" in remnawave_link:
                        short_uuid = remnawave_link.rstrip("/").split("/")[-1]

                    user_data = {
                        "username": email,
                        "trafficLimitStrategy": "NO_RESET",
                        "expireAt": expire_at,
                        "telegramId": tg_id,
                        "activeUserInbounds": inbound_ids,
                    }

                    if traffic_limit_bytes and traffic_limit_bytes > 0:
                        user_data["trafficLimitBytes"] = traffic_limit_bytes * 1024 * 1024 * 1024

                    if short_uuid:
                        user_data["shortUuid"] = short_uuid
                    if hwid_limit is not None:
                        user_data["hwidDeviceLimit"] = hwid_limit

                    result = await remna.create_user(user_data)
                    if not result:
                        logger.error("Ошибка при создании пользователя в Remnawave")
                    else:
                        remnawave_created = True
                        remnawave_key = result.get("subscriptionUrl")
                        remnawave_client_id = result.get("uuid")
                        logger.info(
                            f"[Key Creation] Пользователь создан в Remnawave: {result}"
                        )

        public_link = f"{PUBLIC_LINK}{email}/{tg_id}" if xui_servers else None
        final_client_id = remnawave_client_id or client_id
        logger.info(
            f"[Debug] 3x-ui servers для кластера {cluster_id}: {[s['server_name'] for s in xui_servers]}"
        )

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
                    )
            else:
                await asyncio.gather(
                    *[
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
                        )
                        for server in xui_servers
                    ],
                    return_exceptions=True,
                )

        if (remnawave_created and remnawave_client_id) or xui_servers:
            await store_key(
                session=session,
                tg_id=tg_id,
                client_id=final_client_id,
                email=email,
                expiry_time=expiry_timestamp,
                key=public_link,
                server_id=server_id_to_store,
                remnawave_link=remnawave_key,
                tariff_id=plan,
            )

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
    plan: int = None,
    session=None,
    is_trial: bool = False,
):
    """
    Создает клиента на указанном 3x-ui сервере с лимитом по тарифу или триалу.
    """
    logger.info(
        f"[Client] Вход в create_client_on_server: сервер={server_info.get('server_name')}, план={plan}, is_trial={is_trial}"
    )

    async with semaphore:
        xui = await get_xui_instance(server_info["api_url"])
        inbound_id = server_info.get("inbound_id")
        server_name = server_info.get("server_name", "unknown")

        if not inbound_id:
            logger.warning(f"[Client] INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
            return

        if SUPERNODE:
            unique_email = f"{email}_{server_name.lower()}"
            sub_id = email
        else:
            unique_email = email
            sub_id = unique_email

        total_gb_value = 0
        device_limit_value = None

        if is_trial:
            total_gb_value = TRIAL_CONFIG.get("traffic_limit_gb", 0)
            device_limit_value = TRIAL_CONFIG.get("hwid_limit")
            logger.info(f"[Trial] Используются параметры триала: {total_gb_value} GB, {device_limit_value} устройств")
        elif plan is not None:
            tariff = await get_tariff_by_id(session, plan)
            logger.info(f"[Tariff Debug] Получен тариф: {tariff}")
            if not tariff:
                raise ValueError(f"Тариф с id={plan} не найден.")

            total_gb_value = int(tariff["traffic_limit"]) if tariff["traffic_limit"] else 0
            device_limit_value = int(tariff["device_limit"]) if tariff.get("device_limit") is not None else None

        try:
            logger.info(
                f"[Client] Вызов add_client: email={email}, client_id={client_id}, GB={total_gb_value}, Devices={device_limit_value}"
            )
            traffic_limit_bytes = total_gb_value * 1024 * 1024 * 1024
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
            logger.info(f"[Client] Клиент успешно добавлен на сервер {server_name}")
        except Exception as e:
            logger.error(f"[Client Error] Не удалось создать клиента на {server_name}: {e}")

        if SUPERNODE:
            await asyncio.sleep(0.7)


async def renew_key_in_cluster(
    cluster_id: str,
    email: str,
    client_id: str,
    new_expiry_time: int,
    total_gb: int,
    session: AsyncSession,
    hwid_device_limit: int = None,
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
                raise ValueError(
                    f"Кластер или сервер с ID/именем {cluster_id} не найден."
                )

        result = await session.execute(
            select(Key.tg_id, Key.server_id).where(Key.client_id == client_id).limit(1)
        )
        row = result.first()
        if not row:
            logger.error(
                f"Не найден пользователь с client_id={client_id} в таблице keys."
            )
            return False

        tg_id, server_id = row

        result = await session.execute(
            select(Server.tariff_group).where(Server.server_name == server_id)
        )
        tariff_group_row = result.scalar_one_or_none()

        if tariff_group_row:
            result = await session.execute(
                select(Tariff)
                .where(Tariff.group_code == tariff_group_row, Tariff.is_active is True)
                .order_by(Tariff.duration_days.desc())
                .limit(1)
            )
            tariff = result.scalar_one_or_none()
            if tariff and tariff.device_limit is not None:
                hwid_device_limit = int(tariff.device_limit)

        remnawave_inbound_ids = []
        tasks = []
        for server_info in cluster:
            if server_info.get("panel_type", "3x-ui").lower() == "remnawave":
                inbound_id = server_info.get("inbound_id")
                if inbound_id:
                    remnawave_inbound_ids.append(inbound_id)

        if remnawave_inbound_ids:
            remnawave_server = next(
                (
                    s
                    for s in cluster
                    if s.get("panel_type", "").lower() == "remnawave"
                    and s.get("inbound_id") in remnawave_inbound_ids
                ),
                None,
            )
            if remnawave_server:
                remna = RemnawaveAPI(remnawave_server["api_url"])
                if await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    expire_iso = (
                        datetime.utcfromtimestamp(new_expiry_time // 1000).isoformat()
                        + "Z"
                    )
                    traffic_limit_bytes = total_gb * 1024 * 1024 * 1024 if total_gb else 0
                    updated = await remna.update_user(
                        uuid=client_id,
                        expire_at=expire_iso,
                        active_user_inbounds=remnawave_inbound_ids,
                        traffic_limit_bytes=traffic_limit_bytes,
                        hwid_device_limit=hwid_device_limit,
                    )
                    if updated:
                        logger.info(f"Подписка Remnawave {client_id} успешно продлена")
                        await remna.reset_user_traffic(client_id)
                    else:
                        logger.warning(
                            f"Не удалось продлить подписку Remnawave {client_id}"
                        )
                else:
                    logger.error("Не удалось войти в Remnawave API")

        for server_info in cluster:
            if server_info.get("panel_type", "3x-ui").lower() != "3x-ui":
                continue

            xui = await get_xui_instance(server_info["api_url"])
            inbound_id = server_info.get("inbound_id")
            server_name = server_info.get("server_name", "unknown")

            if not inbound_id:
                logger.warning(
                    f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск."
                )
                continue

            if SUPERNODE:
                unique_email = f"{email}_{server_name.lower()}"
                sub_id = email
            else:
                unique_email = email
                sub_id = unique_email

            traffic_bytes = total_gb * 1024 * 1024 * 1024 if total_gb else 0
            tasks.append(
                extend_client_key(
                    xui=xui,
                    inbound_id=int(inbound_id),
                    email=unique_email,
                    new_expiry_time=new_expiry_time,
                    client_id=client_id,
                    total_gb=traffic_bytes,
                    sub_id=sub_id,
                    tg_id=tg_id,
                    limit_ip=hwid_device_limit,
                )
            )

        await asyncio.gather(*tasks, return_exceptions=True)

        notification_prefixes = ["key_24h", "key_10h", "key_expired", "renew"]
        for notif in notification_prefixes:
            notification_id = f"{email}_{notif}"
            await delete_notification(session, tg_id, notification_id)
        logger.info(f"🧹 Уведомления для ключа {email} очищены при продлении.")

    except Exception as e:
        logger.error(
            f"Не удалось продлить ключ {client_id} в кластере/на сервере {cluster_id}: {e}"
        )
        raise


async def delete_key_from_cluster(
    cluster_id: str, email: str, client_id: str, session: AsyncSession
):
    """Удаление ключа с серверов в кластере или с конкретного сервера"""
    try:
        servers = await get_servers(session)
        cluster = servers.get(cluster_id)

        if not cluster:
            found_servers = []
            for _, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        found_servers.append(server_info)

            if found_servers:
                cluster = found_servers
            else:
                raise ValueError(
                    f"Кластер или сервер с ID/именем {cluster_id} не найден."
                )

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")

            if panel_type == "remnawave":
                remna = RemnawaveAPI(server_info["api_url"])
                if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    logger.error(
                        f"[Remnawave] Не удалось войти на сервер {server_name}"
                    )
                    continue

                success = await remna.delete_user(client_id)
                if success:
                    logger.info(
                        f"[Remnawave] Клиент {client_id} успешно удалён с {server_name}"
                    )
                else:
                    logger.warning(
                        f"[Remnawave] Не удалось удалить клиента {client_id} с {server_name}"
                    )

            elif panel_type == "3x-ui":
                xui = await get_xui_instance(server_info["api_url"])
                inbound_id = server_info.get("inbound_id")

                if not inbound_id:
                    logger.warning(
                        f"[3x-ui] INBOUND_ID отсутствует на сервере {server_name}. Пропуск."
                    )
                    continue

                await delete_client(
                    xui,
                    inbound_id=int(inbound_id),
                    email=email,
                    client_id=client_id,
                )
                logger.info(
                    f"[3x-ui] Клиент {client_id} удалён с сервера {server_name}"
                )

            else:
                logger.warning(
                    f"[Unknown] Неизвестный тип панели '{panel_type}' для сервера {server_name}"
                )

    except Exception as e:
        logger.error(
            f"❌ Ошибка при удалении ключа {client_id} из кластера/сервера {cluster_id}: {e}"
        )
        raise


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
):
    """
    Пересоздаёт ключ на всех серверах указанного кластера (или сервера, если передано имя).
    Работает с панелями 3x-ui и Remnawave. Возвращает кортеж: (новый client_id, remnawave ссылка или None).
    """
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
                raise ValueError(
                    f"Кластер или сервер с ID/именем {cluster_id} не найден."
                )

        expire_iso = (
            datetime.utcfromtimestamp(expiry_time / 1000)
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )

        remnawave_servers = [
            s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"
        ]
        xui_servers = [
            s for s in cluster if s.get("panel_type", "3x-ui").lower() == "3x-ui"
        ]

        remnawave_client_id = None
        remnawave_key = None

        if remnawave_servers:
            inbound_ids = [
                s["inbound_id"] for s in remnawave_servers if s.get("inbound_id")
            ]
            remna = RemnawaveAPI(remnawave_servers[0]["api_url"])
            if await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                await remna.delete_user(client_id)

                group_code = remnawave_servers[0].get("tariff_group")
                if not group_code:
                    raise ValueError("У Remnawave-сервера отсутствует tariff_group")

                result = await session.execute(
                    select(Tariff)
                    .where(Tariff.group_code == group_code, Tariff.is_active is True)
                    .order_by(Tariff.duration_days.desc())
                    .limit(1)
                )
                tariff = result.scalar_one_or_none()

                short_uuid = None
                if remnawave_link and "/" in remnawave_link:
                    short_uuid = remnawave_link.rstrip("/").split("/")[-1]
                    logger.info(f"[Update] Извлечен short_uuid из ссылки: {short_uuid}")

                user_data = {
                    "username": email,
                    "trafficLimitStrategy": "NO_RESET",
                    "expireAt": expire_iso,
                    "telegramId": tg_id,
                    "activeUserInbounds": inbound_ids,
                }
                if traffic_limit is not None:
                    user_data["trafficLimitBytes"] = traffic_limit * 1024 ** 3
                if device_limit is not None:
                    user_data["hwidDeviceLimit"] = device_limit
                if short_uuid:
                    user_data["shortUuid"] = short_uuid
                    logger.info(f"[Update] Добавлен short_uuid в user_data: {short_uuid}")

                result = await remna.create_user(user_data)
                if result:
                    remnawave_client_id = result.get("uuid")
                    remnawave_key = result.get("subscriptionUrl")
                    logger.info(
                        f"[Update] Remnawave: клиент заново создан, новый UUID: {remnawave_client_id}"
                    )
                else:
                    logger.error("[Update] Ошибка создания Remnawave клиента")
            else:
                logger.error("[Update] Не удалось авторизоваться в Remnawave")

        if not remnawave_client_id:
            logger.warning(
                f"[Update] Remnawave client_id не получен. Используется исходный: {client_id}"
            )
            remnawave_client_id = client_id

        tasks = []
        for server_info in xui_servers:
            server_name = server_info.get("server_name", "unknown")
            inbound_id = server_info.get("inbound_id")

            if not inbound_id:
                logger.warning(
                    f"[Update] INBOUND_ID отсутствует для сервера {server_name}. Пропуск."
                )
                continue

            xui = await get_xui_instance(server_info["api_url"])

            sub_id = email
            unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email

            group_code = server_info.get("tariff_group")
            if not group_code:
                raise ValueError(f"У сервера {server_name} отсутствует tariff_group")

            result = await session.execute(
                select(Tariff)
                .where(Tariff.group_code == group_code, Tariff.is_active is True)
                .order_by(Tariff.duration_days.desc())
                .limit(1)
            )
            tariff = result.scalar_one_or_none()

            total_gb_bytes = int(traffic_limit * 1024 ** 3) if traffic_limit else 0
            device_limit_value = device_limit if device_limit is not None else None

            config = ClientConfig(
                client_id=remnawave_client_id,
                email=unique_email,
                tg_id=tg_id,
                limit_ip=device_limit_value,
                total_gb=total_gb_bytes,
                expiry_time=expiry_time,
                enable=True,
                flow="xtls-rprx-vision",
                inbound_id=int(inbound_id),
                sub_id=sub_id,
            )

            tasks.append(add_client(xui, config))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            f"[Update] Ключ {remnawave_client_id} обновлён на всех серверах кластера {cluster_id}"
        )
        return remnawave_client_id, remnawave_key

    except Exception as e:
        logger.error(
            f"[Update Error] Ошибка при обновлении ключа {client_id} на {cluster_id}: {e}"
        )
        raise


async def update_subscription(
    tg_id: int,
    email: str,
    session: AsyncSession,
    cluster_override: str = None,
    country_override: str = None,
    remnawave_link: str = None,
) -> None:
    result = await session.execute(
        select(Key).where(Key.tg_id == tg_id, Key.email == email)
    )
    record = result.scalar_one_or_none()

    if not record:
        raise ValueError(f"The key {email} does not exist in database")

    expiry_time = record.expiry_time
    client_id = record.client_id
    old_cluster_id = record.server_id
    tariff_id = record.tariff_id
    remnawave_link = remnawave_link or record.remnawave_link
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

    traffic_limit = None
    device_limit = None
    if tariff_id:
        result = await session.execute(
            select(Tariff).where(Tariff.id == tariff_id, Tariff.is_active.is_(True))
        )
        tariff = result.scalar_one_or_none()
        if tariff:
            traffic_limit = int(tariff.traffic_limit) if tariff.traffic_limit is not None else None
            device_limit = int(tariff.device_limit) if tariff.device_limit is not None else None
        else:
            logger.warning(f"[LOG] update_subscription: тариф с id={tariff_id} не найден!")
    else:
        logger.warning(f"[LOG] update_subscription: tariff_id отсутствует!")

    await delete_key_from_cluster(old_cluster_id, email, client_id, session=session)
    await session.execute(delete(Key).where(Key.tg_id == tg_id, Key.email == email))
    await session.commit()

    new_cluster_id = (
        country_override or cluster_override or await get_least_loaded_cluster(session)
    )

    new_client_id, remnawave_key = await update_key_on_cluster(
        tg_id=tg_id,
        client_id=client_id,
        email=email,
        expiry_time=expiry_time,
        cluster_id=new_cluster_id,
        session=session,
        traffic_limit=traffic_limit,
        device_limit=device_limit,
        remnawave_link=remnawave_link
    )

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

    has_xui = any(s.get("panel_type", "").lower() == "3x-ui" for s in cluster_servers)
    final_key_link = public_link if has_xui else None

    await store_key(
        session=session,
        tg_id=tg_id,
        client_id=new_client_id,
        email=email,
        expiry_time=expiry_time,
        key=final_key_link,
        remnawave_link=remnawave_key,
        server_id=new_cluster_id,
        tariff_id=tariff_id,
    )


async def get_user_traffic(
    session: AsyncSession, tg_id: int, email: str
) -> dict[str, Any]:
    """
    Получает трафик пользователя на всех серверах, где у него есть ключ (3x-ui и Remnawave).
    Для Remnawave трафик считается один раз и отображается как "Remnawave (общий):".
    """
    result = await session.execute(
        select(Key.client_id, Key.server_id).where(
            Key.tg_id == tg_id, Key.email == email
        )
    )
    rows = result.all()
    if not rows:
        return {"status": "error", "message": "У пользователя нет активных ключей."}

    server_ids = {row.server_id for row in rows}

    result = await session.execute(
        select(Server).where(
            Server.server_name.in_(server_ids) | Server.cluster_name.in_(server_ids)
        )
    )
    server_rows = result.scalars().all()
    if not server_rows:
        logger.error(f"Не найдено серверов для: {server_ids}")
        return {
            "status": "error",
            "message": f"Серверы не найдены: {', '.join(server_ids)}",
        }

    servers_map = {
        s.server_name: {
            "server_name": s.server_name,
            "cluster_name": s.cluster_name,
            "api_url": s.api_url,
            "panel_type": s.panel_type,
        }
        for s in server_rows
    }

    user_traffic_data = {}
    tasks = []

    remnawave_client_id = None
    remnawave_checked = False
    remnawave_api_url = None

    async def fetch_traffic(server_info: dict, client_id: str) -> tuple[str, Any]:
        server_name = server_info["server_name"]
        api_url = server_info["api_url"]
        panel_type = server_info.get("panel_type", "3x-ui").lower()

        try:
            if panel_type == "3x-ui":
                xui = await get_xui_instance(api_url)
                traffic_info = await get_client_traffic(xui, client_id)
                if traffic_info["status"] == "success" and traffic_info["traffic"]:
                    client_data = traffic_info["traffic"][0]
                    used_gb = (client_data.up + client_data.down) / 1073741824
                    return server_name, round(used_gb, 2)
                else:
                    return server_name, "Ошибка получения трафика"
            else:
                return server_name, f"Неизвестная панель: {panel_type}"
        except Exception as e:
            return server_name, f"Ошибка: {e}"

    for row in rows:
        client_id = row.client_id
        server_id = row.server_id

        matched_servers = [
            s
            for s in servers_map.values()
            if s["server_name"] == server_id or s["cluster_name"] == server_id
        ]
        for server_info in matched_servers:
            panel_type = server_info.get("panel_type", "3x-ui").lower()

            if panel_type == "remnawave" and not remnawave_checked:
                remnawave_client_id = client_id
                remnawave_api_url = server_info["api_url"]
                remnawave_checked = True
            elif panel_type == "3x-ui":
                tasks.append(fetch_traffic(server_info, client_id))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for server, result in results:
        user_traffic_data[server] = result

    if remnawave_client_id and remnawave_api_url:
        try:
            remna = RemnawaveAPI(remnawave_api_url)
            if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                user_traffic_data["Remnawave (общий)"] = "Не удалось авторизоваться"
            else:
                user_data = await remna.get_user_by_uuid(remnawave_client_id)
                if not user_data:
                    user_traffic_data["Remnawave (общий)"] = "Клиент не найден"
                else:
                    used_bytes = user_data.get("usedTrafficBytes", 0)
                    used_gb = round(used_bytes / 1073741824, 2)
                    user_traffic_data["Remnawave (общий)"] = used_gb
        except Exception as e:
            user_traffic_data["Remnawave (общий)"] = f"Ошибка: {e}"

    return {"status": "success", "traffic": user_traffic_data}


async def toggle_client_on_cluster(
    cluster_id: str,
    email: str,
    client_id: str,
    enable: bool = True,
    session: AsyncSession = None,
) -> dict[str, Any]:
    try:
        if session is None:
            raise ValueError(
                "[Cluster Toggle] Не передан объект сессии для toggle_client_on_cluster"
            )
        servers = await get_servers(session)
        cluster = servers.get(cluster_id)

        if not cluster:
            found_servers = []
            for _, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        found_servers.append(server_info)
            if found_servers:
                cluster = found_servers
            else:
                raise ValueError(
                    f"Кластер или сервер с ID/именем '{cluster_id}' не найден."
                )

        results = {}
        tasks = []

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")

            if panel_type == "3x-ui":
                inbound_id = server_info.get("inbound_id")
                if not inbound_id:
                    logger.warning(
                        f"[3x-ui] INBOUND_ID отсутствует для сервера {server_name}. Пропуск."
                    )
                    results[server_name] = False
                    continue

                xui = await get_xui_instance(server_info["api_url"])
                unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email

                tasks.append(
                    toggle_client(xui, int(inbound_id), unique_email, client_id, enable)
                )

            elif panel_type == "remnawave":
                remna = RemnawaveAPI(server_info["api_url"])
                if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    logger.error(
                        f"[Remnawave] Авторизация не удалась на сервере {server_name}"
                    )
                    results[server_name] = False
                    continue

                func = remna.enable_user if enable else remna.disable_user
                tasks.append(func(client_id))

            else:
                logger.warning(
                    f"[Cluster Toggle] Неизвестный тип панели '{panel_type}' на сервере {server_name}. Пропуск."
                )
                results[server_name] = False

        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        for server_info, result in zip(cluster, task_results, strict=False):
            server_name = server_info.get("server_name", "unknown")
            if isinstance(result, Exception):
                logger.error(
                    f"[Cluster Toggle] Ошибка на сервере {server_name}: {result}"
                )
                results[server_name] = False
            else:
                results[server_name] = result

        status = "включен" if enable else "отключен"
        logger.info(
            f"[Cluster Toggle] Клиент {email} {status} на серверах кластера {cluster_id}"
        )
        logger.info(f"[Cluster Toggle DEBUG] Результаты: {results}")

        return {
            "status": "success" if any(results.values()) else "error",
            "results": results,
        }

    except Exception as e:
        logger.error(
            f"[Cluster Toggle] Ошибка при изменении состояния клиента {email} в кластере {cluster_id}: {e}"
        )
        return {"status": "error", "error": str(e)}


async def reset_traffic_in_cluster(
    cluster_id: str, email: str, session: AsyncSession
) -> None:
    try:
        servers = await get_servers(session)
        cluster = servers.get(cluster_id)

        if not cluster:
            found_servers = []
            for _, server_list in servers.items():
                for server_info in server_list:
                    if server_info.get("server_name", "").lower() == cluster_id.lower():
                        found_servers.append(server_info)
            if found_servers:
                cluster = found_servers
            else:
                raise ValueError(
                    f"Кластер или сервер с ID/именем {cluster_id} не найден."
                )

        tasks = []
        remnawave_done = False

        for server_info in cluster:
            panel_type = server_info.get("panel_type", "3x-ui").lower()
            server_name = server_info.get("server_name", "unknown")
            api_url = server_info.get("api_url")
            inbound_id = server_info.get("inbound_id")

            if panel_type == "remnawave" and not remnawave_done:
                result = await session.execute(
                    select(Key.client_id)
                    .where(Key.email == email, Key.server_id == cluster_id)
                    .limit(1)
                )
                row = result.first()

                if not row:
                    logger.warning(
                        f"[Remnawave Reset] client_id не найден для {email} на {server_name}"
                    )
                    continue

                client_id = row[0]

                remna = RemnawaveAPI(api_url)
                if not await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    logger.warning(
                        f"[Reset Traffic] Не удалось авторизоваться в Remnawave ({server_name})"
                    )
                    continue

                tasks.append(remna.reset_user_traffic(client_id))
                remnawave_done = True
                continue

            if panel_type == "3x-ui":
                if not inbound_id:
                    logger.warning(
                        f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск."
                    )
                    continue

                xui = await get_xui_instance(api_url)
                unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email
                tasks.append(xui.client.reset_stats(int(inbound_id), unique_email))
            else:
                logger.warning(
                    f"[Reset Traffic] Неизвестный тип панели '{panel_type}' на {server_name}"
                )

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(
            f"[Reset Traffic] Трафик клиента {email} успешно сброшен в кластере {cluster_id}"
        )

    except Exception as e:
        logger.error(
            f"[Reset Traffic] Ошибка при сбросе трафика клиента {email} в кластере {cluster_id}: {e}"
        )
        raise