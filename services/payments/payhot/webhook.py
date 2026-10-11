from aiohttp import web

from core.settings import payhot_config as config
from core.webhook_abuse import get_webhook_client_ip, is_webhook_ip_blocked, record_webhook_signature_failure
from hooks.hooks import run_hooks
from logger import logger
from services.payments.payhot.api import PaymentError, parse_json, verify_webhook
from services.payments.payhot.service import settle_payment


async def payhot_webhook(request: web.Request) -> web.Response:
    """Проверяет подписанное уведомление PayHOT до обработки платежа."""
    if not config.PAYHOT_API_KEY or not config.PAYHOT_WEBHOOK_SECRET:
        return web.Response(status=503, text="not configured")
    ip = get_webhook_client_ip(request)
    if await is_webhook_ip_blocked(ip):
        return web.Response(status=429)
    if request.content_type != "application/json":
        return web.Response(status=415)
    if request.content_length and request.content_length > 65536:
        return web.Response(status=413)
    try:
        body = bytearray()
        async for chunk in request.content.iter_chunked(8192):
            body.extend(chunk)
            if len(body) > 65536:
                return web.Response(status=413)
        raw = bytes(body)
        if not verify_webhook(raw, request.headers.get("PayHot-Signature", "")):
            await record_webhook_signature_failure(ip)
            return web.Response(status=401, text="invalid signature")
        try:
            event = parse_json(raw)
        except (ValueError, UnicodeError, RecursionError):
            return web.Response(status=400, text="bad json")
        event_type = event.get("event_type")
        event_id = event.get("event_id")
        if (
            event.get("api_version") != "v2"
            or not isinstance(event_type, str)
            or not isinstance(event_id, str)
            or not event_id
        ):
            return web.Response(status=400, text="invalid event")
        if event.get("environment") == "sandbox" or event_type.startswith("sandbox."):
            return web.Response(status=200)
        header_id = request.headers.get("PayHot-Event-Id")
        if header_id and header_id != event_id:
            return web.Response(status=400, text="invalid event id")
        if not event_type.startswith("payment."):
            from database import async_session_maker

            responses = await run_hooks(
                "provider_webhook",
                require_enabled=False,
                raise_on_error=True,
                provider="PAYHOT",
                event=event,
                sessionmaker=async_session_maker,
            )
            for response in responses:
                if isinstance(response, web.Response):
                    return response
            return web.Response(status=200)
        data = event.get("data")
        if not isinstance(data, dict) or data.get("object") != "payment":
            return web.Response(status=400, text="invalid payment")
        if data.get("environment") == "sandbox":
            return web.Response(status=200)
        if not str(data.get("merchant_order_reference") or "").startswith("payhot_"):
            return web.Response(status=200)
        result = await settle_payment(data, event_id=event_id)
        return web.Response(status=200 if result.ok else 500)
    except PaymentError as exc:
        logger.warning("[PayHOT] Уведомление не обработано: {}", exc)
        return web.Response(status=500, text="retry later")
    except Exception as exc:
        logger.error("[PayHOT] Ошибка обработки уведомления: {}", type(exc).__name__)
        return web.Response(status=500, text="retry later")
