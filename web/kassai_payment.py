import hashlib
import json
from aiohttp import web
from database import add_payment, async_session_maker, update_balance
from handlers.payments.utils import send_payment_success_notification
from handlers.payments.kassai import verify_kassai_signature
from config import KASSAI_SECRET_KEY, KASSAI_SHOP_ID
from logger import logger

# Хранилище обработанных платежей для предотвращения дублирования
processed_payments = set()


async def kassai_payment_webhook(request: web.Request):
    """
    Обработчик вебхука от KassaAI для подтверждения оплаты
    Формат: MERCHANT_ID=123&AMOUNT=100&intid=123456&MERCHANT_ORDER_ID=test_order&SIGN=...
    """
    try:
        # Получаем данные из POST запроса (form-data)
        data = await request.post()
        data_dict = dict(data)
        
        # Логируем входящий запрос
        logger.info(f"KassaAI webhook received: {data_dict}")
        
        # Получаем подпись из параметров
        signature = data_dict.get("SIGN")
        
        if not signature:
            logger.error("KassaAI: Нет подписи SIGN в запросе!")
            return web.Response(status=400, text="Signature missing")
        
        # Проверяем подпись согласно документации KassaAI
        if not verify_kassai_webhook_signature(data_dict, signature):
            logger.error("KassaAI: Подпись не прошла проверку!")
            return web.Response(status=400, text="Invalid signature")
        
        # Логируем все важные параметры
        logger.info(
            f"KassaAI webhook details: "
            f"MERCHANT_ID={data_dict.get('MERCHANT_ID')}, "
            f"AMOUNT={data_dict.get('AMOUNT')}, "
            f"intid={data_dict.get('intid')}, "
            f"MERCHANT_ORDER_ID={data_dict.get('MERCHANT_ORDER_ID')}, "
            f"P_EMAIL={data_dict.get('P_EMAIL')}, "
            f"CUR_ID={data_dict.get('CUR_ID')}, "
            f"SIGN={data_dict.get('SIGN')}"
        )
        
        # Получаем необходимые данные
        merchant_order_id = data_dict.get("MERCHANT_ORDER_ID")
        amount = data_dict.get("AMOUNT")
        p_email = data_dict.get("P_EMAIL")
        
        if not amount:
            logger.error(f"KassaAI: отсутствует AMOUNT: {data_dict}")
            return web.Response(status=400, text="Missing required fields")
        
        # Проверяем уникальность платежа по MERCHANT_ORDER_ID (если есть)
        if merchant_order_id and merchant_order_id in processed_payments:
            logger.warning(f"KassaAI: платеж MERCHANT_ORDER_ID={merchant_order_id} уже был обработан. Игнорируем дубликат.")
            return web.Response(status=200, text="YES")  # Возвращаем успех чтобы не было повторных попыток
        
        try:
            # Извлекаем tg_id из email (формат: tg_id@domain)
            if p_email and "@" in p_email:
                tg_id = int(p_email.split("@")[0])
                logger.info(f"KassaAI: извлечен tg_id={tg_id} из email={p_email}")
            else:
                logger.error(f"KassaAI: некорректный email для извлечения tg_id: {p_email}")
                return web.Response(status=400, text="Invalid email format")
            
            amount_float = float(amount)
            logger.info(f"KassaAI: обрабатываем платеж MERCHANT_ORDER_ID={merchant_order_id}, tg_id={tg_id}, amount={amount_float}")
        except (ValueError, TypeError) as e:
            logger.error(f"KassaAI: некорректные данные tg_id или amount: {e}")
            return web.Response(status=400, text="Invalid data format")
        
        # Обновляем баланс пользователя
        async with async_session_maker() as session:
            await update_balance(session, tg_id, amount_float)
            await send_payment_success_notification(tg_id, amount_float, session)
            await add_payment(session, tg_id, amount_float, "kassai")
        
        # Добавляем MERCHANT_ORDER_ID в список обработанных платежей (если есть)
        if merchant_order_id:
            processed_payments.add(merchant_order_id)
        
        logger.info(f"✅ KassaAI: баланс пополнен для пользователя {tg_id} на {amount_float}, MERCHANT_ORDER_ID={merchant_order_id}")
        
        # Возвращаем ответ согласно документации KassaAI
        return web.Response(status=200, text="YES")
        
    except Exception as e:
        logger.error(f"Ошибка обработки KassaAI webhook: {e}")
        return web.Response(status=500, text="Internal server error")


def verify_kassai_webhook_signature(data: dict, signature: str) -> bool:
    """
    Проверка подписи вебхука KassaAI согласно документации FreeKassa
    Формат: MERCHANT_ID:AMOUNT:SECRET_KEY2:MERCHANT_ORDER_ID
    """
    try:
        # Формируем строку для подписи согласно документации FreeKassa:
        # MERCHANT_ID:AMOUNT:SECRET_KEY2:MERCHANT_ORDER_ID
        sign_string = (
            f"{data.get('MERCHANT_ID', '')}:"
            f"{data.get('AMOUNT', '')}:"
            f"{KASSAI_SECRET_KEY}:"  # Используем SECRET_KEY2 для вебхуков
            f"{data.get('MERCHANT_ORDER_ID', '')}"
        )
        
        # Вычисляем MD5 хеш
        expected_signature = hashlib.md5(sign_string.encode('utf-8')).hexdigest()
        
        # Сравниваем подписи (регистронезависимо)
        result = signature.upper() == expected_signature.upper()
        
        if not result:
            logger.error(f"KassaAI webhook signature mismatch:")
            logger.error(f"Expected: {expected_signature}")
            logger.error(f"Received: {signature}")
            logger.error(f"Sign string: {sign_string}")
            logger.error(f"MERCHANT_ID: {data.get('MERCHANT_ID')}")
            logger.error(f"AMOUNT: {data.get('AMOUNT')}")
            logger.error(f"MERCHANT_ORDER_ID: {data.get('MERCHANT_ORDER_ID')}")
        
        return result
        
    except Exception as e:
        logger.error(f"Ошибка проверки подписи KassaAI webhook: {e}")
        return False 