from datetime import datetime, timedelta
from typing import Any
import uuid

from py3xui import AsyncApi

from client import add_client
from config import ADMIN_PASSWORD, ADMIN_USERNAME, CLUSTERS, PUBLIC_LINK, TRIAL_TIME, TOTAL_GB
from database import store_key, use_trial
from handlers.texts import INSTRUCTIONS
from handlers.utils import generate_random_email, get_least_loaded_cluster


async def create_trial_key(tg_id: int, session: Any):
    client_id = str(uuid.uuid4())
    email = generate_random_email()
    public_link = f"{PUBLIC_LINK}{email}/{tg_id}"
    instructions = INSTRUCTIONS
    result = {"key": public_link, "instructions": instructions}
    current_time = datetime.utcnow()
    expiry_time = current_time + timedelta(days=TRIAL_TIME, hours=3)
    expiry_timestamp = int(expiry_time.timestamp() * 1000)

    least_loaded_cluster = await get_least_loaded_cluster()

    for server_id, server_info in CLUSTERS[least_loaded_cluster].items():
        xui = AsyncApi(
            server_info["API_URL"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )

        inbound_id = server_info.get("INBOUND_ID")
        if not inbound_id:
            raise ValueError(f"INBOUND_ID отсутствует для сервера {server_info.get('name', 'unknown')}")

        await add_client(
            xui,
            client_id,
            email,
            tg_id,
            limit_ip=1,
            total_gb=TOTAL_GB,
            expiry_time=expiry_timestamp,
            enable=True,
            flow="xtls-rprx-vision",
            inbound_id=int(inbound_id),
        )

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
