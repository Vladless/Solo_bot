import hashlib
import hmac
import json

from aiohttp import web

from config import PARITYPAY_CALLBACK_SECRET_KEY
from core.webhook_abuse import (
    get_webhook_client_ip,
    is_webhook_ip_blocked,
    record_webhook_signature_failure,
)
from logger import logger
from services.payments.pipeline import (
    ParsedPayment,
    process_cancelled_payment,
    process_success_payment,
)


_PROVIDER = "paritypay"


def _build_signature_string(payload: dict) -> str:
    parts: list[str] = []
    for key in sorted(payload.keys()):
        value = payload[key]
        if value is None:
            parts.append("")
        elif isinstance(value, bool):
            parts.append("1" if value else "0")
        else:
            parts.append(str(value))
    return "".join(parts)


def verify_paritypay_signature(payload: dict, signature: str) -> bool:
    try:
        if not PARITYPAY_CALLBACK_SECRET_KEY:
            logger.error("[ParityPay] PARITYPAY_CALLBACK_SECRET_KEY не настроен, webhook отклонён")
            return False

        sign_string = _build_signature_string(payload)
        expected = hmac.new(
            PARITYPAY_CALLBACK_SECRET_KEY.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        result = hmac.compare_digest(expected, signature)
        if not result:
            logger.error(f"[ParityPay] Signature mismatch. Expected: {expected}, Got: {signature}")
        return result
    except Exception as e:
        logger.error(f"[ParityPay] Ошибка проверки подписи: {e}")
        return False


def _parse_tg_id_from_order(order_id: str) -> int | None:
    if not order_id:
        return None
    parts = order_id.split("_")
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except (ValueError, TypeError):
        return None


async def paritypay_webhook(request: web.Request):
    try:
        ip = get_webhook_client_ip(request)
        if await is_webhook_ip_blocked(ip):
            return web.Response(status=429)

        raw_body = await request.read()
        try:
            data = json.loads(raw_body)
        except Exception as e:
            logger.error(f"[ParityPay] Невалидный JSON в webhook: {e}")
            return web.Response(status=400)

        signature = request.headers.get("X-SIGNATURE") or request.headers.get("X-Signature") or ""
        if not signature:
            logger.error("[ParityPay] Нет заголовка X-SIGNATURE")
            await record_webhook_signature_failure(ip)
            return web.Response(status=400)

        if not verify_paritypay_signature(data, signature):
            await record_webhook_signature_failure(ip)
            return web.Response(status=400)

        logger.info(f"[ParityPay] webhook: {json.dumps(data, ensure_ascii=False)}")

        order_id = str(data.get("order_id") or "")
        invoice_id = str(data.get("id") or "")
        status = str(data.get("status") or "").upper()
        amount_raw = data.get("amount")
        service = data.get("service")

        if not order_id:
            logger.error(f"[ParityPay] Пустой order_id в webhook: {data}")
            return web.Response(status=400)

        try:
            amount = float(amount_raw) if amount_raw is not None else 0.0
        except (TypeError, ValueError):
            amount = 0.0

        tg_id = _parse_tg_id_from_order(order_id)

        if status == "PAID":
            if tg_id is None:
                logger.error(f"[ParityPay] Не удалось определить tg_id из order_id={order_id}")
                return web.Response(status=400)

            metadata_patch = {
                "provider": _PROVIDER,
                "paritypay_invoice_id": invoice_id or None,
                "paritypay_service": service,
            }
            parsed = ParsedPayment(
                payment_id=order_id,
                tg_id=int(tg_id),
                amount=float(amount),
                currency="RUB",
                metadata=metadata_patch,
            )
            result = await process_success_payment(_PROVIDER, parsed, metadata_patch=metadata_patch)
            if not result.ok:
                logger.error(f"[ParityPay] Pipeline вернул ошибку: {result.error}, order_id={order_id}")
                return web.Response(status=500)

            logger.info(f"[ParityPay] Платёж обработан: tg_id={tg_id}, amount={amount:.2f} ₽, order_id={order_id}")
            return web.Response(status=200, text="OK")

        if status in ("EXPIRED", "REFUNDED"):
            new_status = "failed" if status == "EXPIRED" else "refunded"
            parsed = ParsedPayment(
                payment_id=order_id,
                tg_id=int(tg_id) if tg_id is not None else None,
                amount=float(amount),
                currency="RUB",
            )
            await process_cancelled_payment(_PROVIDER, parsed, new_status=new_status)
            logger.warning(f"[ParityPay] Транзакция {status}: order_id={order_id}")
            return web.Response(status=200, text="OK")

        logger.info(f"[ParityPay] Промежуточный статус '{status}' для order_id={order_id}, игнор")
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"[ParityPay] Ошибка в webhook: {e}", exc_info=True)
        return web.Response(status=500)
