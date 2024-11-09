import asyncio
import uuid
from datetime import datetime, timedelta

import asyncpg
from loguru import logger

from config import DATABASE_URL, PUBLIC_LINK, SERVERS
from database import store_key
from handlers.keys.key_utils import create_key_on_server
from handlers.texts import INSTRUCTIONS
from handlers.utils import generate_random_email


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
        expiry_time = current_time + timedelta(days=1, hours=3)
        expiry_timestamp = int(expiry_time.timestamp() * 1000)

        tasks = []
        for server_id in SERVERS:
            task = create_key_on_server(
                server_id, tg_id, client_id, email, expiry_timestamp
            )
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        if all(result.get("success") for result in results):
            await store_key(
                tg_id,
                client_id,
                email,
                expiry_timestamp,
                public_link,
                server_id="all_servers",
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
        else:
            logger.error(
                "Не удалось создать ключ на одном или нескольких серверах.")

    finally:
        await conn.close()
