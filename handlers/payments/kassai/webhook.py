import hashlib
from aiohttp import web
from logger import logger
from config import KASSAI_SHOP_ID, KASSAI_SECRET_KEY
from database import add_payment, async_session_maker, get_payment_by_payment_id, update_balance, update_payment_status
from handlers.payments.utils import send_payment_success_notification


def verify_kassai_signature(data: dict, signature: str) -> bool:
    try:
        sign_string = f"{KASSAI_SHOP_ID}:{data.get('AMOUNT', '')}:{KASSAI_SECRET_KEY}:{data.get('MERCHANT_ORDER_ID', '')}"
        expected_signature = hashlib.md5(sign_string.encode("utf-8")).hexdigest()
        result = signature.upper() == expected_signature.upper()
        if not result:
            logger.error(f"KassaAI signature mismatch. Expected: {expected_signature}, Got: {signature}")
            logger.error(f"Sign string: {sign_string}")
        else:
            logger.info("KassaAI webhook: подпись успешно проверена")
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки подписи KassaAI: {e}")
        return False


async def kassai_webhook(request: web.Request):
    try:
        data = await request.post() 
        logger.info(f"KassaAI webhook received: {dict(data)}")
        signature = data.get('SIGN', '')
        if not signature:
            logger.error("KassaAI webhook: отсутствует подпись")
            return web.Response(status=400)
        if not verify_kassai_signature(data, signature):
            logger.error("KassaAI webhook: неверная подпись")
            return web.Response(status=400)
        
        amount_raw = data.get('AMOUNT')
        order_id = data.get('MERCHANT_ORDER_ID')
        
        if not amount_raw or not order_id:
            logger.error("KassaAI webhook: отсутствуют обязательные параметры")
            return web.Response(status=400)
        
        amount = float(amount_raw)
        
        try:
            tg_id = int(order_id.split('_')[1])
        except (IndexError, ValueError) as e:
            logger.error(f"KassaAI webhook: не удалось извлечь tg_id из order_id {order_id}: {e}")
            return web.Response(status=400)
        
        logger.info(f"KassaAI: успешный платёж {order_id} на сумму {amount} RUB для пользователя {tg_id}")
        
        async with async_session_maker() as session:
            payment = await get_payment_by_payment_id(session, order_id)
            if payment:
                if payment.get("status") == "success":
                    logger.info(f"KassaAI: платёж {order_id} уже обработан")
                    return web.Response(text="OK")
                ok = await update_payment_status(session=session, internal_id=int(payment["id"]), new_status="success")
                if not ok:
                    logger.error(f"KassaAI: не удалось обновить статус платежа {order_id}")
                    return web.Response(status=500)
                await update_balance(session, tg_id, amount)
                await send_payment_success_notification(tg_id, amount, session)
                await session.commit()
            else:
                await add_payment(
                    session=session,
                    tg_id=tg_id,
                    amount=amount,
                    payment_system="KASSAI",
                    status="success",
                    currency="RUB",
                    payment_id=order_id,
                    metadata=None,
                )
                await update_balance(session, tg_id, amount)
                await send_payment_success_notification(tg_id, amount, session)
                await session.commit()
        logger.info(f"KassaAI: платёж {order_id} успешно обработан, баланс пользователя {tg_id} пополнен на {amount} RUB")
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Ошибка обработки KassaAI webhook: {e}")
        return web.Response(status=500)
