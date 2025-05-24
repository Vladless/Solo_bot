from aiohttp.web_urldispatcher import UrlDispatcher

import bot
from config import TBLOCKER_WEBHOOK_PATH

from .tblocker import tblocker_webhook


async def register_web_routes(router: UrlDispatcher) -> None:
    router.add_post(TBLOCKER_WEBHOOK_PATH, tblocker_webhook)
