import hashlib
import json
from aiohttp import web
from database import add_payment, async_session_maker, update_balance
from handlers.payments.utils import send_payment_success_notification
from handlers.payments.kassai import verify_kassai_signature
from config import KASSAI_SECRET_KEY, KASSAI_SHOP_ID
from logger import logger

processed_payments = set()


async def kassai_payment_webhook(request: web.Request):
    """
    Обработчик вебхука от KassaAI для подтверждения оплаты
    """
    try:
        data = await request.post()
        data_dict = dict(data)
        
        logger.info(f"KassaAI webhook received from {request.remote}")
        
        signature = data_dict.get("SIGN")
        if not signature:
            logger.error("KassaAI: Missing SIGN in request")
            return web.Response(status=400, text="Signature missing")
        
        if not verify_kassai_webhook_signature(data_dict, signature):
            logger.error("KassaAI: Invalid signature")
            return web.Response(status=400, text="Invalid signature")
        
        merchant_order_id = data_dict.get("MERCHANT_ORDER_ID")
        amount = data_dict.get("AMOUNT")
        p_email = data_dict.get("P_EMAIL")
        
        if not amount:
            logger.error(f"KassaAI: Missing AMOUNT")
            return web.Response(status=400, text="Missing required fields")
        
        if merchant_order_id and merchant_order_id in processed_payments:
            logger.warning(f"KassaAI: Duplicate payment {merchant_order_id}")
            return web.Response(status=200, text="YES")
        
        try:
            if p_email and "@" in p_email:
                tg_id = int(p_email.split("@")[0])
            else:
                logger.error(f"KassaAI: Invalid email format: {p_email}")
                return web.Response(status=400, text="Invalid email format")
            
            amount_float = float(amount)
        except (ValueError, TypeError) as e:
            logger.error(f"KassaAI: Invalid data format: {e}")
            return web.Response(status=400, text="Invalid data format")
        
        async with async_session_maker() as session:
            await update_balance(session, tg_id, amount_float)
            await send_payment_success_notification(tg_id, amount_float, session)
            await add_payment(session, tg_id, amount_float, "kassai")
        
        if merchant_order_id:
            processed_payments.add(merchant_order_id)
        
        logger.info(f"✅ KassaAI: Payment processed for user {tg_id}, amount {amount_float}")
        return web.Response(status=200, text="YES")
        
    except Exception as e:
        logger.error(f"KassaAI webhook error: {e}")
        return web.Response(status=500, text="Internal server error")


def verify_kassai_webhook_signature(data: dict, signature: str) -> bool:
    """
    Проверка подписи вебхука KassaAI согласно документации FreeKassa
    """
    try:
        sign_string = (
            f"{data.get('MERCHANT_ID', '')}:"
            f"{data.get('AMOUNT', '')}:"
            f"{KASSAI_SECRET_KEY}:"
            f"{data.get('MERCHANT_ORDER_ID', '')}"
        )
        
        expected_signature = hashlib.md5(sign_string.encode('utf-8')).hexdigest()
        result = signature.upper() == expected_signature.upper()
        
        if not result:
            logger.error(f"KassaAI webhook signature mismatch")
        
        return result
        
    except Exception as e:
        logger.error(f"KassaAI signature verification error: {e}")
        return False 