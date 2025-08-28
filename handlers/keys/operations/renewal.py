import asyncio

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from database import delete_notification, get_servers
from database.models import Key, Server, Tariff
from database.notifications import clear_hot_lead_notifications
from logger import logger
from panels.remnawave import RemnawaveAPI
from panels.three_xui import ClientConfig, add_client, extend_client_key, get_xui_instance


async def renew_key_in_cluster(
    cluster_id: str,
    email: str,
    client_id: str,
    new_expiry_time: int,
    total_gb: int,
    session: AsyncSession,
    hwid_device_limit: int = 0,
    reset_traffic: bool = True,
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

        result = await session.execute(select(Key.tg_id, Key.server_id).where(Key.client_id == client_id).limit(1))
        row = result.first()
        if not row:
            logger.error(f"Не найден пользователь с client_id={client_id} в таблице keys.")
            return False

        tg_id, server_id = row

        result = await session.execute(select(Server.tariff_group).where(Server.server_name == server_id))
        tariff_group_row = result.scalar_one_or_none()

        if tariff_group_row:
            result = await session.execute(
                select(Tariff)
                .where(Tariff.group_code == tariff_group_row, Tariff.is_active.is_(True))
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
                    if s.get("panel_type", "").lower() == "remnawave" and s.get("inbound_id") in remnawave_inbound_ids
                ),
                None,
            )
            if remnawave_server:
                remna = RemnawaveAPI(remnawave_server["api_url"])
                if await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                    expire_iso = datetime.utcfromtimestamp(new_expiry_time // 1000).isoformat() + "Z"
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
                        if reset_traffic:
                            await remna.reset_user_traffic(client_id)
                    else:
                        logger.warning(f"Не удалось продлить подписку Remnawave {client_id}, пробуем создать")
                        result = await session.execute(
                            select(Key.remnawave_link, Key.key).where(Key.client_id == client_id)
                        )
                        row = result.one_or_none()
                        remnawave_link = row[0] if row else None
                        row[1] if row else None

                        user_data = {
                            "username": email,
                            "trafficLimitStrategy": "NO_RESET",
                            "expireAt": expire_iso,
                            "telegramId": tg_id,
                            "activeInternalSquads": remnawave_inbound_ids,
                        }
                        if remnawave_link and "/" in remnawave_link:
                            user_data["shortUuid"] = remnawave_link.rstrip("/").split("/")[-1]
                        if traffic_limit_bytes and traffic_limit_bytes > 0:
                            user_data["trafficLimitBytes"] = traffic_limit_bytes
                        if hwid_device_limit is not None:
                            user_data["hwidDeviceLimit"] = hwid_device_limit

                        result = await remna.create_user(user_data)
                        if result:
                            new_client_id = result.get("uuid")
                            new_remnawave_link = result.get("subscriptionUrl")
                            logger.info(f"Пользователь Remnawave {client_id} успешно создан")

                            await session.execute(
                                update(Key)
                                .where(Key.client_id == client_id)
                                .values(client_id=new_client_id, remnawave_link=new_remnawave_link)
                            )
                            await session.commit()
                        else:
                            logger.error(f"Не удалось создать пользователя Remnawave {client_id}")
                else:
                    logger.error("Не удалось войти в Remnawave API")

        for server_info in cluster:
            if server_info.get("panel_type", "3x-ui").lower() != "3x-ui":
                continue

            xui = await get_xui_instance(server_info["api_url"])
            inbound_id = server_info.get("inbound_id")
            server_name = server_info.get("server_name", "unknown")

            if not inbound_id:
                logger.warning(f"INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                continue

            if SUPERNODE:
                unique_email = f"{email}_{server_name.lower()}"
                sub_id = email
            else:
                unique_email = email
                sub_id = unique_email

            traffic_bytes = total_gb * 1024 * 1024 * 1024 if total_gb else 0

            async def update_or_create_client(xui, inbound_id, unique_email, sub_id, server_name):
                updated = await extend_client_key(
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

                if not updated:
                    logger.warning(f"Не удалось обновить клиента {unique_email}, пробуем создать")
                    config = ClientConfig(
                        client_id=client_id,
                        email=unique_email,
                        tg_id=tg_id,
                        limit_ip=hwid_device_limit if hwid_device_limit is not None else 0,
                        total_gb=traffic_bytes,
                        expiry_time=new_expiry_time,
                        enable=True,
                        flow="xtls-rprx-vision",
                        inbound_id=int(inbound_id),
                        sub_id=sub_id,
                    )
                    await add_client(xui, config)

            tasks.append(update_or_create_client(xui, inbound_id, unique_email, sub_id, server_name))

        await asyncio.gather(*tasks, return_exceptions=True)

        notification_prefixes = ["key_24h", "key_10h", "key_expired", "renew"]
        for notif in notification_prefixes:
            notification_id = f"{email}_{notif}"
            await delete_notification(session, tg_id, notification_id)
        logger.info(f"🧹 Уведомления для ключа {email} очищены при продлении.")

        try:
            await clear_hot_lead_notifications(session, tg_id)
        except Exception as e:
            logger.warning(f"Не удалось очистить уведомления о скидках для {tg_id} при продлении: {e}")

    except Exception as e:
        logger.error(f"Не удалось продлить ключ {client_id} в кластере/на сервере {cluster_id}: {e}")
        raise
