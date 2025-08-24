import base64
import json

import aiohttp

from aiohttp import web
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from database import add_payment, async_session_maker, update_balance
from handlers.payments.utils import send_payment_success_notification
from logger import logger


PUBLIC_KEY_URL = "https://api.wata.pro/api/h2h/public-key"


async def get_wata_public_key():
    async with aiohttp.ClientSession() as session:
        async with session.get(PUBLIC_KEY_URL) as resp:
            data = await resp.json()
            return data["value"].encode()


async def verify_signature(raw_json: bytes, signature: str, public_key_pem: bytes) -> bool:
    try:
        public_key = serialization.load_pem_public_key(public_key_pem, backend=default_backend())
        signature_bytes = base64.b64decode(signature)
        public_key.verify(signature_bytes, raw_json, padding.PKCS1v15(), hashes.SHA512())
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки подписи WATA: {e}")
        return False


async def wata_payment_webhook(request: web.Request):
    try:
        raw_json = await request.read()
        data = json.loads(raw_json)
        signature = request.headers.get("X-Signature")
        if not signature:
            logger.error("Нет подписи X-Signature в заголовке!")
            return web.Response(status=400)
        public_key_pem = await get_wata_public_key()
        if not await verify_signature(raw_json, signature, public_key_pem):
            logger.error("Подпись WATA не прошла проверку!")
            return web.Response(status=400)

        logger.info(f"WATA webhook: {json.dumps(data, ensure_ascii=False)}")
        logger.info(
            f"transactionId={data.get('transactionId')}, status={data.get('transactionStatus')}, orderId={data.get('orderId')}, amount={data.get('amount')}, currency={data.get('currency')}, errorCode={data.get('errorCode')}, errorDescription={data.get('errorDescription')}"
        )
        if data.get("transactionStatus") == "Paid":
            tg_id = data.get("orderId")
            amount = data.get("amount")
            if not tg_id or not amount:
                logger.error(f"WATA: отсутствует orderId или amount: {data}")
                return web.Response(status=400)
            async with async_session_maker() as session:
                await update_balance(session, int(tg_id), float(amount))
                await add_payment(session, int(tg_id), float(amount), "wata")
                await send_payment_success_notification(tg_id, float(amount), session)
            logger.info(f"✅ WATA: баланс пополнен для пользователя {tg_id} на {amount}")
        elif data.get("transactionStatus") == "Declined":
            logger.warning(f"WATA: транзакция отклонена: {data}")
        else:
            logger.warning(f"WATA: неизвестный статус транзакции: {data.get('transactionStatus')}")
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка в webhook WATA: {e}")
        return web.Response(status=500)
