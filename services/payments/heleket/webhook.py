import base64
import hashlib
import json

from aiohttp import web

from config import HELEKET_API_KEY
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


_PROVIDER = "heleket"


def verify_heleket_signature(data: dict) -> bool:
    """Проверяет MD5-подпись webhook от Heleket."""
    try:
        if not HELEKET_API_KEY:
            logger.error("Heleket webhook: HELEKET_API_KEY не настроен, webhook отклонён")
            return False

        received_signature = data.get("sign")
        if not received_signature:
            logger.error("Heleket webhook: отсутствует подпись")
            return False

        data_without_sign = data.copy()
        del data_without_sign["sign"]

        json_data = json.dumps(data_without_sign, ensure_ascii=False, separators=(",", ":"))
        json_data = json_data.replace("/", "\\/")
        base64_data = base64.b64encode(json_data.encode("utf-8")).decode("utf-8")
        sign_string = base64_data + HELEKET_API_KEY
        calculated_signature = hashlib.md5(sign_string.encode("utf-8")).hexdigest()
        is_valid = calculated_signature.lower() == received_signature.lower()

        if not is_valid:
            logger.error(
                f"Heleket webhook: неверная подпись. Ожидалось: {calculated_signature}, получено: {received_signature}"
            )
        else:
            logger.info("Heleket webhook: подпись успешно проверена")
        return is_valid
    except Exception as e:
        logger.error(f"Ошибка проверки подписи Heleket webhook: {e}")
        return False


def _extract_tg_and_amount(order_id: str, additional_data, merchant_amount) -> tuple[int | None, float | None]:
    """Извлекает tg_id и сумму для зачисления (rub_amount или merchant_amount)."""
    tg_id = None
    rub_amount = None
    if additional_data:
        try:
            for part in str(additional_data).split(","):
                if part.startswith("tg_id:"):
                    tg_id = int(part.split(":")[1])
                elif part.startswith("rub_amount:"):
                    rub_amount = float(part.split(":")[1])
        except Exception as e:
            logger.error(f"Ошибка парсинга additional_data: {e}")
    if not tg_id and order_id and "_" in order_id:
        try:
            tg_id = int(order_id.split("_")[1])
        except Exception as e:
            logger.error(f"Ошибка извлечения tg_id из order_id: {e}")
    balance_amount = rub_amount if rub_amount else (float(merchant_amount) if merchant_amount else None)
    return tg_id, balance_amount


async def process_heleket_webhook(data: dict) -> bool:
    """Обрабатывает уже верифицированный webhook от Heleket."""
    try:
        logger.info(f"Processing Heleket webhook: {data}")

        webhook_type = data.get("type")
        order_id = data.get("order_id")
        status = data.get("status")

        if webhook_type != "payment":
            logger.warning(f"Heleket webhook: неизвестный тип {webhook_type}")
            return False

        if status in ["paid", "paid_over"]:
            tg_id, balance_amount = _extract_tg_and_amount(
                order_id=order_id,
                additional_data=data.get("additional_data"),
                merchant_amount=data.get("merchant_amount"),
            )
            if not tg_id:
                logger.error(f"Не удалось извлечь tg_id из Heleket webhook: {data}")
                return False
            if balance_amount is None:
                logger.error(f"Не удалось определить сумму зачисления: {data}")
                return False

            parsed = ParsedPayment(
                payment_id=str(order_id),
                tg_id=tg_id,
                amount=balance_amount,
                currency="USD",
            )
            result = await process_success_payment(_PROVIDER, parsed)
            return result.ok

        if status in ["fail", "wrong_amount", "cancel", "system_fail"]:
            logger.warning(f"Heleket: неудачный платёж {order_id}, статус: {status}")
            parsed = ParsedPayment(
                payment_id=str(order_id),
                tg_id=None,
                amount=0.0,
                currency="USD",
            )
            result = await process_cancelled_payment(_PROVIDER, parsed, new_status="failed")
            return result.ok

        logger.info(f"Heleket: промежуточный статус {status} для платежа {order_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка обработки Heleket webhook: {e}")
        return False


async def heleket_webhook(request: web.Request):
    """Обработчик webhook от Heleket для aiohttp."""
    try:
        ip = get_webhook_client_ip(request)
        if await is_webhook_ip_blocked(ip):
            return web.Response(status=429)
        data = await request.json()
        logger.info(f"Heleket webhook received from {request.remote}")

        if not verify_heleket_signature(data):
            logger.error("Heleket webhook: неверная подпись")
            await record_webhook_signature_failure(ip)
            return web.Response(status=400, text="Invalid signature")

        success = await process_heleket_webhook(data)
        if success:
            return web.Response(status=200, text="OK")
        return web.Response(status=400, text="Processing failed")
    except Exception as e:
        logger.error(f"Ошибка обработки Heleket webhook: {e}")
        return web.Response(status=500, text="Internal server error")
