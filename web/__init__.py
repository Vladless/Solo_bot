from aiohttp.web_urldispatcher import UrlDispatcher

import bot

from config import TBLOCKER_WEBHOOK_PATH

from .tblocker import tblocker_webhook
from .wata_payment import wata_payment_webhook
from .kassai_payment import kassai_payment_webhook
from .heleket_payment import heleket_payment_webhook
from .cryptocloud_payment import cryptocloud_payment_webhook


WATA_WEBHOOK_PATH = "/wata/webhook"
KASSAI_WEBHOOK_PATH = "/kassai/webhook"
HELEKET_WEBHOOK_PATH = "/heleket/webhook"
CRYPTOCLOUD_WEBHOOK_PATH = "/cryptocloud/webhook"


async def register_web_routes(router: UrlDispatcher) -> None:
    router.add_post(TBLOCKER_WEBHOOK_PATH, tblocker_webhook)
    router.add_post(WATA_WEBHOOK_PATH, wata_payment_webhook)
    router.add_post(KASSAI_WEBHOOK_PATH, kassai_payment_webhook)
    router.add_post(HELEKET_WEBHOOK_PATH, heleket_payment_webhook)
    router.add_post(CRYPTOCLOUD_WEBHOOK_PATH, cryptocloud_payment_webhook)
