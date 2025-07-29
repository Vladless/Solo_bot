from aiohttp.web_urldispatcher import UrlDispatcher

from config import TBLOCKER_WEBHOOK_PATH

from .heleket_payment import heleket_payment_webhook
from .kassai_payment import kassai_payment_webhook
from .tblocker import tblocker_webhook
from .wata_payment import wata_payment_webhook


WATA_WEBHOOK_PATH = "/wata/webhook"
KASSAI_WEBHOOK_PATH = "/kassai/webhook"
HELEKET_WEBHOOK_PATH = "/heleket/webhook"


async def register_web_routes(router: UrlDispatcher) -> None:
    router.add_post(TBLOCKER_WEBHOOK_PATH, tblocker_webhook)
    router.add_post(WATA_WEBHOOK_PATH, wata_payment_webhook)
    router.add_post(KASSAI_WEBHOOK_PATH, kassai_payment_webhook)
    router.add_post(HELEKET_WEBHOOK_PATH, heleket_payment_webhook)
