import hashlib

from aiohttp import web

from config import KASSAI_SECRET_KEY, KASSAI_SHOP_ID, KASSAI_WEBHOOK_RESPONSE
from core.webhook_abuse import (
    get_webhook_client_ip,
    is_webhook_ip_blocked,
    record_webhook_signature_failure,
)
from logger import logger
from services.payments.pipeline import ParsedPayment, process_success_payment


_PROVIDER = "kassai"


def verify_kassai_signature(data: dict, signature: str) -> bool:
    """Проверяет MD5-подпись webhook от KassaAI."""
    try:
        if not KASSAI_SECRET_KEY or not KASSAI_SHOP_ID:
            logger.error("KassaAI webhook: KASSAI_SECRET_KEY/KASSAI_SHOP_ID не настроены, webhook отклонён")
            return False

        sign_string = (
            f"{KASSAI_SHOP_ID}:{data.get('AMOUNT', '')}:{KASSAI_SECRET_KEY}:{data.get('MERCHANT_ORDER_ID', '')}"
        )
        expected_signature = hashlib.md5(sign_string.encode("utf-8")).hexdigest()
        result = signature.upper() == expected_signature.upper()
        if not result:
            logger.error(f"KassaAI signature mismatch. Expected: {expected_signature}, Got: {signature}")
        else:
            logger.info("KassaAI webhook: подпись успешно проверена")
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки подписи KassaAI: {e}")
        return False


def _parse_kassai(data) -> ParsedPayment | None:
    amount_raw = data.get("AMOUNT")
    order_id = data.get("MERCHANT_ORDER_ID")
    if not amount_raw or not order_id:
        return None
    try:
        tg_id = int(str(order_id).split("_")[1])
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
        logger.info(f"KassaAI webhook received: {dict(data)}")

        signature = data.get("SIGN", "")
        if not signature:
            await record_webhook_signature_failure(ip)
            return web.Response(status=400)

        if not verify_kassai_signature(data, signature):
            await record_webhook_signature_failure(ip)
            return web.Response(status=400)

        parsed = _parse_kassai(data)
        if parsed is None:
            return web.Response(status=400)

        result = await process_success_payment(_PROVIDER, parsed)
        if not result.ok:
            return web.Response(status=500)

        return web.Response(text=KASSAI_WEBHOOK_RESPONSE)
    except Exception as e:
        logger.error(f"Ошибка обработки KassaAI webhook: {e}")
        return web.Response(status=500)
