import base64
import hashlib
import json

from aiohttp import web

from config import HELEKET_API_KEY, HELEKET_CURRENCY_RATE
from database import add_payment, async_session_maker, update_balance
from handlers.payments.utils import send_payment_success_notification
from logger import logger


processed_payments = set()


async def heleket_payment_webhook(request: web.Request):
    """
    Обработчик вебхука от Heleket для подтверждения оплаты
    """
    try:
        data = await request.json()

        logger.info(f"Heleket webhook received from {request.remote}")
        logger.info(f"Heleket webhook data: {data}")

        signature = data.get("sign", "")
        if not verify_heleket_webhook_signature(data, signature):
            logger.error("Heleket: Invalid signature")
            return web.Response(status=400, text="Invalid signature")

        uuid = data.get("uuid")
        order_id = data.get("order_id")
        payment_status = data.get("status")
        amount = data.get("amount")
        payment_amount = data.get("payment_amount")
        additional_data = data.get("additional_data", "")

        logger.info(
            f"Heleket payment: uuid={uuid}, order_id={order_id}, status={payment_status}, amount={amount}, payment_amount={payment_amount}"
        )

        if payment_status != "paid":
            logger.info(f"Heleket: Payment not completed, status={payment_status}")
            return web.Response(status=200, text="OK")

        if not amount or not uuid:
            logger.error("Heleket: Missing amount or uuid")
            return web.Response(status=400, text="Missing required fields")

        if uuid in processed_payments:
            logger.warning(f"Heleket: Duplicate payment uuid={uuid}")
            return web.Response(status=200, text="OK")

        if order_id and order_id in processed_payments:
            logger.warning(f"Heleket: Duplicate payment order_id={order_id}")
            return web.Response(status=200, text="OK")

        tg_id = None
        rub_amount = None
        try:
            if additional_data and "tg_id:" in additional_data:
                parts = additional_data.split(",")
                for part in parts:
                    if part.startswith("tg_id:"):
                        tg_id = int(part.split("tg_id:")[1])
                    elif part.startswith("rub_amount:"):
                        rub_amount = float(part.split("rub_amount:")[1])

            elif order_id and "_" in order_id:
                tg_id = int(order_id.split("_")[1])
                usd_amount = float(amount)
                rub_amount = usd_amount * HELEKET_CURRENCY_RATE
            else:
                logger.error("Heleket: Cannot extract tg_id from data")
                return web.Response(status=400, text="Cannot extract user ID")

        except (ValueError, IndexError) as e:
            logger.error(f"Heleket: Error extracting tg_id or rub_amount: {e}")
            return web.Response(status=400, text="Invalid user ID or amount format")

        if not rub_amount:
            logger.error("Heleket: Could not determine rub_amount")
            return web.Response(status=400, text="Cannot determine amount")

        async with async_session_maker() as session:
            await update_balance(session, tg_id, rub_amount)
            await send_payment_success_notification(tg_id, rub_amount, session)
            await add_payment(session, tg_id, rub_amount, "heleket")

        processed_payments.add(uuid)
        if order_id:
            processed_payments.add(order_id)

        logger.info(f"✅ Heleket: Payment processed for user {tg_id}, amount {rub_amount} RUB (${amount}), uuid={uuid}")
        return web.Response(status=200, text="OK")

    except Exception as e:
        logger.error(f"Heleket webhook error: {e}")
        return web.Response(status=500, text="Internal server error")


def verify_heleket_webhook_signature(data: dict, signature: str) -> bool:
    """
    Проверка подписи вебхука Heleket
    """
    try:
        data_without_sign = {k: v for k, v in data.items() if k != "sign"}

        json_data = json.dumps(data_without_sign, separators=(",", ":"))
        base64_data = base64.b64encode(json_data.encode("utf-8")).decode("utf-8")
        sign_string = base64_data + HELEKET_API_KEY
        expected_signature = hashlib.md5(sign_string.encode("utf-8")).hexdigest()

        result = signature.upper() == expected_signature.upper()

        if not result:
            logger.error("Heleket webhook signature mismatch")
            logger.error(f"Expected: {expected_signature}, Got: {signature}")
            logger.error(f"Base64 data: {base64_data}")
            logger.error(f"Sign string: {sign_string}")

        return result

    except Exception as e:
        logger.error(f"Heleket signature verification error: {e}")
        return False
