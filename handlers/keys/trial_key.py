from datetime import datetime, timedelta
from typing import Any
import uuid

from py3xui import AsyncApi

from client import add_client
from config import ADMIN_PASSWORD, ADMIN_USERNAME, CLUSTERS, PUBLIC_LINK, TRIAL_TIME
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
    for server_id, server in CLUSTERS[least_loaded_cluster].items():
        xui = AsyncApi(
            CLUSTERS[least_loaded_cluster][server_id]["API_URL"],
            username=ADMIN_USERNAME,
            password=ADMIN_PASSWORD,
        )

        await add_client(
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

    await store_key(
        tg_id,
        client_id,
        email,
        expiry_timestamp,
        public_link,
        server_id=least_loaded_cluster,
    )
    await use_trial(tg_id, session)
    return result
