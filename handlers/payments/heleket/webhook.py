import base64
import hashlib
import json
from logger import logger
from config import HELEKET_API_KEY
from database import async_session_maker, update_payment_status, add_balance_to_user


def verify_heleket_signature(data: dict) -> bool:
    try:
        received_signature = data.get('sign')
        if not received_signature:
            logger.error("Heleket webhook: отсутствует подпись")
            return False
        
        data_without_sign = data.copy()
        del data_without_sign['sign']
        
        json_data = json.dumps(data_without_sign, ensure_ascii=False, separators=(',', ':'))
        json_data = json_data.replace('/', '\\/')
        base64_data = base64.b64encode(json_data.encode('utf-8')).decode('utf-8')
        sign_string = base64_data + HELEKET_API_KEY
        calculated_signature = hashlib.md5(sign_string.encode('utf-8')).hexdigest()
        is_valid = calculated_signature.lower() == received_signature.lower()
        
        if not is_valid:
            logger.error(f"Heleket webhook: неверная подпись. Ожидалось: {calculated_signature}, получено: {received_signature}")
            logger.error(f"Heleket webhook: строка для подписи: {sign_string}")
        else:
            logger.info("Heleket webhook: подпись успешно проверена")
        return is_valid
    except Exception as e:
        logger.error(f"Ошибка проверки подписи Heleket webhook: {e}")
        return False


async def process_heleket_webhook(data: dict) -> bool:
    try:
        logger.info(f"Processing Heleket webhook: {data}")
        
        webhook_type = data.get('type')
        uuid = data.get('uuid')
        order_id = data.get('order_id')
        status = data.get('status')
        amount = data.get('amount')
        payment_amount = data.get('payment_amount')
        merchant_amount = data.get('merchant_amount')
        currency = data.get('currency')
        payer_currency = data.get('payer_currency')
        additional_data = data.get('additional_data')
        is_final = data.get('is_final', False)
        
        logger.info(f"Heleket webhook - Type: {webhook_type}, UUID: {uuid}, Order: {order_id}, Status: {status}")
        if webhook_type != 'payment':
            logger.warning(f"Heleket webhook: неизвестный тип {webhook_type}")
            return False
        if status in ['paid', 'paid_over']:
            logger.info(f"Heleket: успешный платёж {order_id} на сумму {payment_amount} {payer_currency}")
            tg_id = None
            rub_amount = None
            if additional_data:
                try:
                    for part in additional_data.split(','):
                        if part.startswith('tg_id:'):
                            tg_id = int(part.split(':')[1])
                        elif part.startswith('rub_amount:'):
                            rub_amount = float(part.split(':')[1])
                except Exception as e:
                    logger.error(f"Ошибка парсинга additional_data: {e}")
            if not tg_id and '_' in order_id:
                try:
                    tg_id = int(order_id.split('_')[1])
                except Exception as e:
                    logger.error(f"Ошибка извлечения tg_id из order_id: {e}")
            if not tg_id:
                logger.error(f"Не удалось извлечь tg_id из Heleket webhook: {data}")
                return False
            balance_amount = rub_amount if rub_amount else float(merchant_amount)
            async with async_session_maker() as session:
                await update_payment_status(session, order_id, "success")
                await add_balance_to_user(session, tg_id, balance_amount)
                await session.commit()
            logger.info(f"Heleket: платёж {order_id} для пользователя {tg_id} успешно обработан, баланс пополнен на {balance_amount} RUB")
            return True
        elif status in ['fail', 'wrong_amount', 'cancel', 'system_fail']:
            logger.warning(f"Heleket: неудачный платёж {order_id}, статус: {status}")
            
            async with async_session_maker() as session:
                await update_payment_status(session, order_id, "failed")
                await session.commit()
            return True
        else:
            logger.info(f"Heleket: промежуточный статус {status} для платежа {order_id}")
            return True 
    except Exception as e:
        logger.error(f"Ошибка обработки Heleket webhook: {e}")
        return False
