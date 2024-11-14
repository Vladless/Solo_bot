import asyncio
import uuid
from datetime import datetime, timedelta

import asyncpg
from py3xui import AsyncApi

from client import add_client
from config import ADMIN_PASSWORD, ADMIN_USERNAME, CLUSTERS, DATABASE_URL, PUBLIC_LINK, TRIAL_TIME
from database import store_key
from handlers.texts import INSTRUCTIONS
from handlers.utils import generate_random_email, get_least_loaded_cluster


async def create_trial_key(tg_id: int):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        client_id = str(uuid.uuid4())
        email = generate_random_email()

        public_link = f"{PUBLIC_LINK}{email}"
        instructions = INSTRUCTIONS

        result = {"key": public_link, "instructions": instructions}

        asyncio.create_task(
            generate_and_store_keys(tg_id, client_id, email, public_link)
        )

        return result

    finally:
        await conn.close()


async def generate_and_store_keys(
    tg_id: int, client_id: str, email: str, public_link: str
):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        current_time = datetime.utcnow()
        expiry_time = current_time + timedelta(days=TRIAL_TIME, hours=3)
        expiry_timestamp = int(expiry_time.timestamp() * 1000)

        least_loaded_cluster = await get_least_loaded_cluster()

        tasks = []
        for server_id, server in CLUSTERS[least_loaded_cluster].items():
            task = create_key_on_server(
                least_loaded_cluster,
                server_id,
                client_id,
                email,
                tg_id,
                expiry_timestamp,
            )
            tasks.append(task)

        await asyncio.gather(*tasks)

        await store_key(
            tg_id,
            client_id,
            email,
            expiry_timestamp,
            public_link,
            server_id=least_loaded_cluster,
        )

        await conn.execute(
            """
            INSERT INTO connections (tg_id, trial) 
            VALUES ($1, 1) 
            ON CONFLICT (tg_id) 
            DO UPDATE SET trial = 1
            """,
            tg_id,
        )
    finally:
        await conn.close()


async def create_key_on_server(
    cluster_id: str,
    server_id: str,
    client_id: str,
    email: str,
    tg_id: int,
    expiry_timestamp: int,
):
    """Создает ключ на сервере в указанном кластере и возвращает результат."""

    xui = AsyncApi(
        CLUSTERS[cluster_id][server_id]["API_URL"],
        username=ADMIN_USERNAME,
        password=ADMIN_PASSWORD,
    )

    response = await add_client(
        xui,
        client_id,
        email,
        tg_id,
        limit_ip=1,
        total_gb=0,
        expiry_time=expiry_timestamp,
        enable=True,
        flow="xtls-rprx-vision",
    )

    return response
