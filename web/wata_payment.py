import base64
import aiohttp
from aiohttp import web
import json
import logging
from database import async_session_maker, update_balance, add_payment
from handlers.payments.utils import send_payment_success_notification
from config import WATA_RU_TOKEN, WATA_SBP_TOKEN, WATA_INT_TOKEN
from logger import logger
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

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
        public_key.verify(
            signature_bytes,
            raw_json,
            padding.PKCS1v15(),
            hashes.SHA512()
        )
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
        # Проверяем статус оплаты
        if data.get("transactionStatus") != "Paid":
            logger.warning(f"WATA: транзакция не оплачена: {data}")
            return web.Response(status=200)
        tg_id = data.get("orderId")
        amount = data.get("amount")
        if not tg_id or not amount:
            logger.error(f"WATA: отсутствует orderId или amount: {data}")
            return web.Response(status=400)
        async with async_session_maker() as session:
            await update_balance(session, int(tg_id), float(amount))
            await send_payment_success_notification(tg_id, float(amount), session)
            await add_payment(session, int(tg_id), float(amount), "wata")
        logger.info(f"✅ WATA: баланс пополнен для пользователя {tg_id} на {amount}")
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Ошибка в webhook WATA: {e}")
        return web.Response(status=500)
