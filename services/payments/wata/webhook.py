import base64
import json

import aiohttp

from aiohttp import web
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from core.redis_cache import cache_get, cache_set
from core.webhook_abuse import (
    get_webhook_client_ip,
    is_webhook_ip_blocked,
    record_webhook_signature_failure,
)
from database import async_session_maker, get_payment_by_payment_id
from logger import logger
from services.payments.pipeline import (
    ParsedPayment,
    process_cancelled_payment,
    process_success_payment,
)


_PROVIDER = "wata"

_WATA_PUBLIC_KEY_URL = "https://api.wata.pro/api/h2h/public-key"
_WATA_PUBLIC_KEY_CACHE_KEY = "wata:public_key:pem"
_WATA_PUBLIC_KEY_CACHE_TTL = 6 * 60 * 60


async def _get_wata_public_key() -> bytes:
    try:
        cached = await cache_get(_WATA_PUBLIC_KEY_CACHE_KEY)
        if isinstance(cached, str) and cached:
            return cached.encode()
    except Exception:
        pass

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(_WATA_PUBLIC_KEY_URL) as resp:
            data = await resp.json()
            pem_str = str(data["value"])

    try:
        await cache_set(_WATA_PUBLIC_KEY_CACHE_KEY, pem_str, _WATA_PUBLIC_KEY_CACHE_TTL)
    except Exception:
        pass

    return pem_str.encode()


async def _verify_signature(raw_json: bytes, signature: str, public_key_pem: bytes) -> bool:
    try:
        public_key = serialization.load_pem_public_key(public_key_pem, backend=default_backend())
        signature_bytes = base64.b64decode(signature)
        public_key.verify(signature_bytes, raw_json, padding.PKCS1v15(), hashes.SHA512())
        return True
    except Exception as e:
        logger.error(f"[WATA] Ошибка проверки подписи: {e}")
        return False


def _parse_wata_order_id(order_id: str) -> tuple[int | None, float | None]:
    if not order_id:
        return None, None
    parts = order_id.split("_")
    if len(parts) >= 3:
        try:
            return int(parts[1]), float(parts[2])
        except (ValueError, TypeError):
            return None, None
    if len(parts) == 2:
        try:
            return int(parts[1]), None
        except (ValueError, TypeError):
            return None, None
    return None, None


async def wata_webhook(request: web.Request):
    try:
        ip = get_webhook_client_ip(request)
        if await is_webhook_ip_blocked(ip):
            return web.Response(status=429)

        raw_json = await request.read()
        try:
            data = json.loads(raw_json)
        except Exception as e:
            logger.error(f"[WATA] Невалидный JSON в webhook: {e}")
            return web.Response(status=400)

        signature = request.headers.get("X-Signature")
        if not signature:
            logger.error("[WATA] Нет подписи X-Signature в заголовке")
            await record_webhook_signature_failure(ip)
            return web.Response(status=400)

        try:
            public_key_pem = await _get_wata_public_key()
        except Exception as e:
            logger.error(f"[WATA] Не удалось получить публичный ключ: {e}")
            return web.Response(status=503)

        if not await _verify_signature(raw_json, signature, public_key_pem):
            logger.error("[WATA] Подпись не прошла проверку")
            await record_webhook_signature_failure(ip)
            return web.Response(status=400)

        logger.info(f"[WATA] webhook: {json.dumps(data, ensure_ascii=False)}")

        tx_status = data.get("transactionStatus")
        order_id = str(data.get("orderId") or "")
        transaction_id = str(data.get("transactionId") or "")
        webhook_currency = str(data.get("currency") or "RUB").upper()
        webhook_amount = data.get("amount")

        if not order_id:
            logger.error(f"[WATA] Пустой orderId в webhook: {data}")
            return web.Response(status=400)

        async with async_session_maker() as lookup_session:
            pending = await get_payment_by_payment_id(lookup_session, order_id)

        tg_id: int | None = None
        rub_amount: float = 0.0
        cassa_name: str | None = None

        fb_tg_id, fb_rub = _parse_wata_order_id(order_id)
        if fb_tg_id is not None:
            tg_id = fb_tg_id

        if pending:
            try:
                rub_amount = float(pending["amount"])
            except (TypeError, ValueError, KeyError):
                pass
            meta = pending.get("metadata") or {}
            cassa_name = meta.get("cassa") if isinstance(meta, dict) else None
        else:
            if fb_rub and fb_rub > 0:
                rub_amount = fb_rub

        if tx_status == "Paid":
            if tg_id is None:
                logger.error(f"[WATA] Не удалось определить tg_id для orderId={order_id}")
                return web.Response(status=400)

            if rub_amount <= 0:
                if webhook_currency == "RUB":
                    try:
                        raw_amount = float(webhook_amount or 0)
                        raw_commission = float(data.get("commission") or 0)
                        rub_amount = round(raw_amount - raw_commission, 2)
                    except Exception:
                        rub_amount = 0.0
                if rub_amount <= 0:
                    logger.error(f"[WATA] Не удалось определить RUB-сумму для зачисления: orderId={order_id}")
                    return web.Response(status=400)

            metadata_patch = {
                "provider": _PROVIDER,
                "wata_transaction_id": transaction_id or None,
                "wata_currency": webhook_currency,
                "wata_amount": webhook_amount,
                "wata_commission": data.get("commission"),
                "cassa": cassa_name,
            }

            update_currency: str | None = None
            update_original_amount: float | None = None
            if webhook_currency != "RUB":
                update_currency = webhook_currency
                try:
                    update_original_amount = float(webhook_amount) if webhook_amount is not None else None
                except (TypeError, ValueError):
                    update_original_amount = None

            parsed = ParsedPayment(
                payment_id=order_id,
                tg_id=int(tg_id),
                amount=float(rub_amount),
                currency="RUB",
                metadata=metadata_patch,
            )

            result = await process_success_payment(
                _PROVIDER,
                parsed,
                metadata_patch=metadata_patch,
                update_currency=update_currency,
                update_original_amount=update_original_amount,
            )
            if not result.ok:
                logger.error(f"[WATA] Pipeline вернул ошибку: {result.error}, orderId={order_id}")
                return web.Response(status=500)

            logger.info(
                f"[WATA] Платёж обработан: tg_id={tg_id}, amount={rub_amount:.2f} ₽, "
                f"orderId={order_id}, transactionId={transaction_id}"
            )
            return web.Response(status=200, text="OK")

        if tx_status == "Declined":
            parsed_amount = float(rub_amount) if rub_amount > 0 else 0.0
            if parsed_amount <= 0:
                try:
                    parsed_amount = float(webhook_amount or 0)
                except Exception:
                    parsed_amount = 0.0

            parsed = ParsedPayment(
                payment_id=order_id,
                tg_id=int(tg_id) if tg_id is not None else None,
                amount=parsed_amount,
                currency=webhook_currency,
            )
            await process_cancelled_payment(_PROVIDER, parsed, new_status="failed")

            logger.warning(f"[WATA] Транзакция отклонена: orderId={order_id}")
            return web.Response(status=200, text="OK")

        logger.warning(f"[WATA] Неизвестный статус транзакции: {tx_status}")
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"[WATA] Ошибка в webhook: {e}", exc_info=True)
        return web.Response(status=500)
