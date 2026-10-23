import hashlib
import hmac

from aiohttp import web

from core.webhook_abuse import (
    get_webhook_client_ip,
    is_webhook_ip_blocked,
    record_webhook_signature_failure,
)
from logger import logger
from services.payments.owner_refs import parse_payment_owner
from services.payments.pipeline import ParsedPayment, process_success_payment
from settings.config import KASSAI_SECRET_KEY, KASSAI_SHOP_ID, KASSAI_WEBHOOK_RESPONSE


_PROVIDER = "kassai"


def verify_kassai_signature(data: dict, signature: str) -> bool:
    """Проверяет подпись уведомления KassaAI."""
    try:
        if not KASSAI_SECRET_KEY or not KASSAI_SHOP_ID:
            logger.error("KassaAI webhook: KASSAI_SECRET_KEY/KASSAI_SHOP_ID не настроены, webhook отклонён")
            return False
        if str(data.get("MERCHANT_ID") or "") != str(KASSAI_SHOP_ID):
            return False

        sign_string = (
            f"{KASSAI_SHOP_ID}:{data.get('AMOUNT', '')}:{KASSAI_SECRET_KEY}:{data.get('MERCHANT_ORDER_ID', '')}"
        )
        expected_signature = hashlib.md5(sign_string.encode("utf-8"), usedforsecurity=False).hexdigest()
        return hmac.compare_digest(str(signature).upper(), expected_signature.upper())
    except Exception as e:
        logger.error(f"Ошибка проверки подписи KassaAI: {e}")
        return False


def verify_kassai_recurrent_signature(data: dict, signature: str) -> bool:
    """Проверяет подпись отдельного уведомления привязки СБП."""
    if not KASSAI_SECRET_KEY or not KASSAI_SHOP_ID:
        return False
    if not isinstance(signature, str) or len(signature) != 32 or not signature.isascii():
        return False
    if str(data.get("MERCHANT_ID") or "") != str(KASSAI_SHOP_ID):
        return False
    if any(not isinstance(value, str) for value in data.values()):
        return False
    values = ":".join(data[key] for key in sorted(data) if key != "SIGN")
    expected = hashlib.md5((values + KASSAI_SECRET_KEY).encode("utf-8"), usedforsecurity=False).hexdigest()
    return hmac.compare_digest(str(signature).lower(), expected)


def _parse_kassai(data) -> ParsedPayment | None:
    amount_raw = data.get("AMOUNT")
    order_id = data.get("MERCHANT_ORDER_ID")
    if not amount_raw or not order_id:
        return None
    try:
        tg_id = parse_payment_owner(str(order_id).split("_")[1])
        amount = float(amount_raw)
    except (IndexError, ValueError) as e:
        logger.error(f"KassaAI webhook: не удалось извлечь tg_id/amount из order_id={order_id}: {e}")
        return None
    return ParsedPayment(
        payment_id=str(order_id),
        tg_id=tg_id,
        amount=amount,
        currency="RUB",
    )


async def kassai_webhook(request: web.Request):
    try:
        ip = get_webhook_client_ip(request)
        if await is_webhook_ip_blocked(ip):
            return web.Response(status=429)

        data = await request.post()
        if len(data) != len(set(data.keys())):
            return web.Response(status=400)
        data = dict(data)

        signature = data.get("SIGN", "")
        if not signature:
            await record_webhook_signature_failure(ip)
            return web.Response(status=400)

        event_type = str(data.get("TYPE") or "")
        if event_type and event_type != "KG_RECURRENT_INIT":
            return web.Response(status=400)
        verifier = verify_kassai_recurrent_signature if event_type == "KG_RECURRENT_INIT" else verify_kassai_signature
        if not verifier(data, signature):
            await record_webhook_signature_failure(ip)
            return web.Response(status=400)

        from services.payments.kassai_autopay.service import process_payment_webhook, process_recurrent_init

        if event_type == "KG_RECURRENT_INIT":
            result = await process_recurrent_init(data)
            return web.Response(text=KASSAI_WEBHOOK_RESPONSE) if result.ok else web.Response(status=400)

        result = await process_payment_webhook(data)
        if result is not None:
            return web.Response(text=KASSAI_WEBHOOK_RESPONSE) if result.ok else web.Response(status=500)

        parsed = _parse_kassai(data)
        if parsed is None:
            return web.Response(status=400)

        result = await process_success_payment(_PROVIDER, parsed)
        if not result.ok:
            return web.Response(status=500)

        return web.Response(text=KASSAI_WEBHOOK_RESPONSE)
    except Exception:
        logger.error("Ошибка обработки KassaAI webhook")
        return web.Response(status=500)
