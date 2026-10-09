import json

from aiohttp import web

from core.settings import kassa2328_config as config
from core.webhook_abuse import get_webhook_client_ip, is_webhook_ip_blocked, record_webhook_signature_failure
from logger import logger
from services.payments.kassa2328.api import PaymentError, verify_webhook
from services.payments.kassa2328.service import settle_payment


def _unique_object(pairs: list[tuple]) -> dict:
    """Отклоняет неоднозначные повторяющиеся поля JSON."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющееся поле JSON")
        result[key] = value
    return result


def _reject_constant(value: str):
    """Отклоняет нечисловые константы JSON."""
    raise ValueError("Некорректная константа JSON")


async def kassa2328_webhook(request: web.Request) -> web.Response:
    """Проверяет и принимает уведомления об оплате 2328."""
    if not config.KASSA2328_API_KEY or not config.KASSA2328_PROJECT_UUID:
        return web.Response(status=503, text="not configured")
    ip = get_webhook_client_ip(request)
    if await is_webhook_ip_blocked(ip):
        return web.Response(status=429)
    if request.content_type != "application/json":
        return web.Response(status=415, text="expected json")
    if request.content_length and request.content_length > 65536:
        return web.Response(status=413)
    try:
        body = bytearray()
        async for chunk in request.content.iter_chunked(8192):
            body.extend(chunk)
            if len(body) > 65536:
                return web.Response(status=413)
        data = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        return web.Response(status=400, text="bad json")
    if not verify_webhook(data):
        await record_webhook_signature_failure(ip)
        return web.Response(status=401, text="invalid signature")
    try:
        await settle_payment(data)
    except PaymentError as exc:
        logger.warning("[2328] Уведомление не обработано: {}", exc)
        return web.Response(status=500, text="retry later")
    except Exception as exc:
        logger.error("[2328] Ошибка обработки уведомления: {}", type(exc).__name__)
        return web.Response(status=500, text="retry later")
    return web.Response(text="ok")
