from aiohttp.web_urldispatcher import UrlDispatcher

from handlers.payments.heleket.webhook import heleket_webhook
from handlers.payments.kassai.webhook import kassai_webhook
from utils.modules_loader import load_module_webhooks

from .wata_payment import wata_payment_webhook


WATA_WEBHOOK_PATH = "/wata/webhook"
KASSAI_WEBHOOK_PATH = "/kassai/webhook"
HELEKET_WEBHOOK_PATH = "/heleket/webhook"


async def register_web_routes(router: UrlDispatcher) -> None:
    router.add_post(WATA_WEBHOOK_PATH, wata_payment_webhook)
    router.add_post(KASSAI_WEBHOOK_PATH, kassai_webhook)
    router.add_post(HELEKET_WEBHOOK_PATH, heleket_webhook)

    try:
        module_webhooks = load_module_webhooks()

        for webhook_data in module_webhooks:
            path = webhook_data.get("path")
            handler = webhook_data.get("handler")
            if path and handler:
                router.add_post(path, handler)
                print(f"[Web] Зарегистрирован вебхук модуля: {path}")
    except Exception as e:
        print(f"[Web] Ошибка при загрузке вебхуков модулей: {e}")
