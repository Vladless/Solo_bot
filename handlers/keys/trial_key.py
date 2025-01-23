import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytz
from py3xui import AsyncApi

from client import ClientConfig, add_client
from config import ADMIN_PASSWORD, ADMIN_USERNAME, LIMIT_IP, PUBLIC_LINK, SUPERNODE, TOTAL_GB, TRIAL_TIME
from database import get_servers, get_trial, store_key, set_trial
from handlers.texts import INSTRUCTIONS
from handlers.utils import generate_random_email, get_least_loaded_cluster
from logger import logger


async def create_trial_key(tg_id: int, session: Any):
    try:
        trial_status = await get_trial(tg_id, session)
        if trial_status == 1:
            return {"error": "Вы уже использовали пробную версию."}
    except Exception as e:
        logger.error(f"Ошибка при проверке триала: {e}")

    client_id = str(uuid.uuid4())
    base_email = generate_random_email()
    public_link = f"{PUBLIC_LINK}{base_email}/{tg_id}"
    instructions = INSTRUCTIONS
    result = {"key": public_link, "instructions": instructions, "email": base_email}

    moscow_tz = pytz.timezone("Europe/Moscow")
    current_time = datetime.now(moscow_tz)
    expiry_time = current_time + timedelta(days=TRIAL_TIME)
    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    clusters = await get_servers(session)
    least_loaded_cluster = await get_least_loaded_cluster()
    if least_loaded_cluster not in clusters:
        raise ValueError(f"Кластер {least_loaded_cluster} не найден в базе данных.")

    servers_in_cluster = clusters[least_loaded_cluster]
    tasks = []

    for server_info in servers_in_cluster:
        server_name = server_info.get("server_name", "unknown")

        if SUPERNODE:
            email = f"{base_email}_{server_name.lower()}"
        else:
            email = base_email

        tasks.append(
            add_client(
                AsyncApi(
                    server_info["api_url"],
                    username=ADMIN_USERNAME,
                    password=ADMIN_PASSWORD,
                ),
                ClientConfig(
                    client_id=client_id,
                    email=email,
                    tg_id=tg_id,
                    limit_ip=LIMIT_IP,
                    total_gb=TOTAL_GB,
                    expiry_time=expiry_timestamp,
                    enable=True,
                    flow="xtls-rprx-vision",
                    inbound_id=int(server_info["inbound_id"]),
                    sub_id=base_email,
                ),
            )
        )

    await asyncio.gather(*tasks)

    await store_key(
        tg_id,
        client_id,
        base_email,
        expiry_timestamp,
        public_link,
        server_id=least_loaded_cluster,
        session=session,
    )

    await set_trial(tg_id, 1, session)
    return result
