import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

from config import ADMIN_PASSWORD, ADMIN_USERNAME, PUBLIC_LINK, TOTAL_GB, TRIAL_TIME
from handlers.texts import INSTRUCTIONS
from py3xui import AsyncApi

from client import add_client
from database import get_servers_from_db, store_key, use_trial
from handlers.utils import generate_random_email, get_least_loaded_cluster
from logger import logger


async def create_trial_key(tg_id: int, session: Any):
    try:
        # Проверка статуса триала
        trial_status = await session.fetchval(
            'SELECT trial FROM connections WHERE tg_id = $1',
            tg_id
        )

        if trial_status == 1:
            return {'error': 'Вы уже использовали пробную версию.'}

    except Exception as e:
        logger.error(f"Ошибка при при проверке триала: {e}")

    client_id = str(uuid.uuid4())
    email = generate_random_email()
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"
    instructions = INSTRUCTIONS
    result = {"key": public_link, "instructions": instructions, "email": email}
    current_time = datetime.utcnow()
    expiry_time = current_time + timedelta(days=TRIAL_TIME)
    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    clusters = await get_servers_from_db()
    least_loaded_cluster = await get_least_loaded_cluster()

    if least_loaded_cluster not in clusters:
        raise ValueError(f"Кластер {least_loaded_cluster} не найден в базе данных.")

    servers_in_cluster = clusters[least_loaded_cluster]
    tasks = []

    for server_info in servers_in_cluster:
        tasks.append(
            add_client(
                AsyncApi(
                    server_info["api_url"],
                    username=ADMIN_USERNAME,
                    password=ADMIN_PASSWORD,
                ),
                client_id,
                email,
                tg_id,
                limit_ip=1,
                total_gb=TOTAL_GB,
                expiry_time=expiry_timestamp,
                enable=True,
                flow="xtls-rprx-vision",
                inbound_id=int(server_info["inbound_id"]),
            )
        )

    await asyncio.gather(*tasks)

    await store_key(
        tg_id,
        client_id,
        email,
        expiry_timestamp,
        public_link,
        server_id=least_loaded_cluster,
        session=session,
    )

    await use_trial(tg_id, session)

    return result
