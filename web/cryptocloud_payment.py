import jwt
import json
from aiohttp import web
from database import add_payment, async_session_maker, update_balance
from handlers.payments.utils import send_payment_success_notification
from config import CRYPTOCLOUD_SECRET, CRYPTOCLOUD_CURRENCY_RATE
from logger import logger

processed_payments = set()


async def cryptocloud_payment_webhook(request: web.Request):
    """
    Обработчик вебхука от CryptoCloud для подтверждения оплаты
    """
    try:
        data = await request.post()
        
        logger.info(f"CryptoCloud webhook received from {request.remote}")
        logger.info(f"CryptoCloud webhook data: {dict(data)}")
        
        status = data.get("status", "")
        invoice_id = data.get("invoice_id", "")
        amount_crypto = data.get("amount_crypto", "")
        currency = data.get("currency", "")
        order_id = data.get("order_id", "")
        token = data.get("token", "")
        
        if not verify_cryptocloud_jwt_token(token):
            logger.error("CryptoCloud: Invalid JWT token")
            return web.Response(status=400, text="Invalid token")
        
        if status != "success":
            logger.info(f"CryptoCloud: Payment not completed, status={status}")
            return web.Response(status=200, text="OK")
        
        if not invoice_id or not amount_crypto:
            logger.error(f"CryptoCloud: Missing invoice_id or amount_crypto")
            return web.Response(status=400, text="Missing required fields")
        
        if invoice_id in processed_payments:
            logger.warning(f"CryptoCloud: Duplicate payment invoice_id={invoice_id}")
            return web.Response(status=200, text="OK")
        
        if order_id and order_id in processed_payments:
            logger.warning(f"CryptoCloud: Duplicate payment order_id={order_id}")
            return web.Response(status=200, text="OK")
        
        tg_id = None
        rub_amount = None
        try:
            if order_id and "_" in order_id:
                parts = order_id.split("_")
                if len(parts) >= 3:
                    tg_id = int(parts[1])
                    rub_amount = float(parts[2])
                elif len(parts) == 2:
                    tg_id = int(parts[1])
                else:
                    logger.error(f"CryptoCloud: Invalid order_id format: {order_id}")
                    return web.Response(status=400, text="Invalid order_id format")
            else:
                logger.error(f"CryptoCloud: Cannot extract tg_id from order_id: {order_id}")
                return web.Response(status=400, text="Cannot extract user ID")
        except (ValueError, IndexError) as e:
            logger.error(f"CryptoCloud: Error extracting tg_id: {e}")
            return web.Response(status=400, text="Invalid user ID format")
        
        if rub_amount is None:
            try:
                if 'invoice_info' in data:
                    invoice_info = json.loads(data.get('invoice_info', '{}'))
                    usd_amount = float(invoice_info.get('amount_usd', 0))
                else:
                    usd_amount = float(amount_crypto)
                
                rub_amount = usd_amount * CRYPTOCLOUD_CURRENCY_RATE
            except (ValueError, TypeError) as e:
                logger.error(f"CryptoCloud: Invalid amount format: {e}")
                return web.Response(status=400, text="Invalid amount format")
            
        async with async_session_maker() as session:
            await update_balance(session, tg_id, rub_amount)
            await send_payment_success_notification(tg_id, rub_amount, session)
            await add_payment(session, tg_id, rub_amount, "cryptocloud")
        
        processed_payments.add(invoice_id)
        if order_id:
            processed_payments.add(order_id)
        
        logger.info(f"✅ CryptoCloud: Payment processed for user {tg_id}, amount {rub_amount} RUB (${usd_amount}), invoice_id={invoice_id}")
        return web.Response(status=200, text="OK")
        
    except Exception as e:
        logger.error(f"CryptoCloud webhook error: {e}")
        return web.Response(status=500, text="Internal server error")


def verify_cryptocloud_jwt_token(token: str) -> bool:
    """
    Проверка JWT токена от CryptoCloud
    """
    try:
        payload = jwt.decode(token, CRYPTOCLOUD_SECRET, algorithms=['HS256'])
        logger.info(f"CryptoCloud JWT payload: {payload}")
        return True
    except jwt.ExpiredSignatureError:
        logger.error("CryptoCloud JWT token expired")
        return False
    except jwt.InvalidTokenError as e:
        logger.error(f"CryptoCloud JWT token invalid: {e}")
        return False
    except Exception as e:
        logger.error(f"CryptoCloud JWT verification error: {e}")
        return False 