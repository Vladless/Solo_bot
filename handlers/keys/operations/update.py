import asyncio

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import PUBLIC_LINK, REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD, SUPERNODE
from database import get_servers, store_key
from database.models import Key, Tariff
from handlers.utils import get_least_loaded_cluster
from logger import logger
from panels._3xui import ClientConfig, add_client, get_xui_instance
from panels.remnawave import RemnawaveAPI

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
                raise ValueError(f"Кластер или сервер с ID/именем {cluster_id} не найден.")

        expire_iso = datetime.utcfromtimestamp(expiry_time / 1000).replace(tzinfo=timezone.utc).isoformat()

        remnawave_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "remnawave"]
        xui_servers = [s for s in cluster if s.get("panel_type", "3x-ui").lower() == "3x-ui"]

        remnawave_client_id = None
        remnawave_key = None

        if remnawave_servers:
            inbound_ids = [s["inbound_id"] for s in remnawave_servers if s.get("inbound_id")]
            remna = RemnawaveAPI(remnawave_servers[0]["api_url"])
            if await remna.login(REMNAWAVE_LOGIN, REMNAWAVE_PASSWORD):
                await remna.delete_user(client_id)

                group_code = remnawave_servers[0].get("tariff_group")
                if not group_code:
                    raise ValueError("У Remnawave-сервера отсутствует tariff_group")

                result = await session.execute(
                    select(Tariff)
                    .where(Tariff.group_code == group_code, Tariff.is_active.is_(True))
                    .order_by(Tariff.duration_days.desc())
                    .limit(1)
                )
                result.scalar_one_or_none()

                short_uuid = None
                if remnawave_link and "/" in remnawave_link:
                    short_uuid = remnawave_link.rstrip("/").split("/")[-1]
                    logger.info(f"[Update] Извлечен short_uuid из ссылки: {short_uuid}")

                user_data = {
                    "username": email,
                    "trafficLimitStrategy": "NO_RESET",
                    "expireAt": expire_iso,
                    "telegramId": tg_id,
                    "activeInternalSquads": inbound_ids,
                }
                if traffic_limit is not None:
                    user_data["trafficLimitBytes"] = traffic_limit * 1024**3
                if device_limit is not None:
                    user_data["hwidDeviceLimit"] = device_limit
                if short_uuid:
                    user_data["shortUuid"] = short_uuid
                    logger.info(f"[Update] Добавлен short_uuid в user_data: {short_uuid}")

                result = await remna.create_user(user_data)
                if result:
                    remnawave_client_id = result.get("uuid")
                    remnawave_key = result.get("subscriptionUrl")
                    logger.info(f"[Update] Remnawave: клиент заново создан, новый UUID: {remnawave_client_id}")
                else:
                    logger.error("[Update] Ошибка создания Remnawave клиента")
            else:
                logger.error("[Update] Не удалось авторизоваться в Remnawave")

        if not remnawave_client_id:
            logger.warning(f"[Update] Remnawave client_id не получен. Используется исходный: {client_id}")
            remnawave_client_id = client_id

        tasks = []
        for server_info in xui_servers:
            server_name = server_info.get("server_name", "unknown")
            inbound_id = server_info.get("inbound_id")

            if not inbound_id:
                logger.warning(f"[Update] INBOUND_ID отсутствует для сервера {server_name}. Пропуск.")
                continue

            xui = await get_xui_instance(server_info["api_url"])

            sub_id = email
            unique_email = f"{email}_{server_name.lower()}" if SUPERNODE else email

            group_code = server_info.get("tariff_group")
            if not group_code:
                raise ValueError(f"У сервера {server_name} отсутствует tariff_group")

            result = await session.execute(
                select(Tariff)
                .where(Tariff.group_code == group_code, Tariff.is_active.is_(True))
                .order_by(Tariff.duration_days.desc())
                .limit(1)
            )
            result.scalar_one_or_none()

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
                flow="xtls-rprx-vision",
                inbound_id=int(inbound_id),
                sub_id=sub_id,
            )

            tasks.append(add_client(xui, config))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"[Update] Ключ {remnawave_client_id} обновлён на всех серверах кластера {cluster_id}")
        return remnawave_client_id, remnawave_key

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
    result = await session.execute(select(Key).where(Key.tg_id == tg_id, Key.email == email))
    record = result.scalar_one_or_none()

    if not record:
        raise ValueError(f"The key {email} does not exist in database")

    expiry_time = record.expiry_time
    client_id = record.client_id
    old_cluster_id = record.server_id
    tariff_id = record.tariff_id
    alias = record.alias
    remnawave_link = remnawave_link or record.remnawave_link
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"

    traffic_limit = None
    device_limit = None
    if tariff_id:
        result = await session.execute(select(Tariff).where(Tariff.id == tariff_id, Tariff.is_active.is_(True)))
        tariff = result.scalar_one_or_none()
        if tariff:
            traffic_limit = int(tariff.traffic_limit) if tariff.traffic_limit is not None else None
            device_limit = int(tariff.device_limit) if tariff.device_limit is not None else 0
        else:
            logger.warning(f"[LOG] update_subscription: тариф с id={tariff_id} не найден!")
    else:
        logger.warning("[LOG] update_subscription: tariff_id отсутствует!")

    await delete_key_from_cluster(old_cluster_id, email, client_id, session=session)
    await session.execute(delete(Key).where(Key.tg_id == tg_id, Key.email == email))
    await session.commit()

    if country_override or cluster_override:
        new_cluster_id = country_override or cluster_override
    else:
        try:
            new_cluster_id = await get_least_loaded_cluster(session)
        except ValueError:
            logger.warning("[Update] Нет доступных кластеров, оставляем на старом")
            new_cluster_id = old_cluster_id

    new_client_id, remnawave_key = await update_key_on_cluster(
        tg_id=tg_id,
        client_id=client_id,
        email=email,
        expiry_time=expiry_time,
        cluster_id=new_cluster_id,
        session=session,
        traffic_limit=traffic_limit,
        device_limit=device_limit,
        remnawave_link=remnawave_link,
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
        alias=alias,
    )
