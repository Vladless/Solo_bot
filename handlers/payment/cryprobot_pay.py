from aiocryptopay import AioCryptoPay, Networks
from aiohttp import web
from loguru import logger

from config import CRYPTO_BOT_ENABLE, CRYPTO_BOT_TOKEN

if CRYPTO_BOT_ENABLE:
    crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET)


async def cryptobot_webhook(request):
    try:
        data = await request.json()
        logger.info(f"Получены данные вебхука: {data}")
        if data.get("update_type") == "invoice_paid":
            await process_crypto_payment(data["payload"])
            return web.Response(status=200)
        else:
            logger.warning(f"Неподдерживаемый тип обновления: {data.get('update_type')}")
            return web.Response(status=400)
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}")
        return web.Response(status=500)


async def process_crypto_payment(payload):
    if payload["status"] == "paid":
        custom_payload = payload["payload"]
        user_id, sub_type = custom_payload.split(":")
        # TODO
    else:
        logger.warning(f"Получен неоплаченный инвойс: {payload}")
