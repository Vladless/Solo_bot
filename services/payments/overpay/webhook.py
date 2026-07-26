import base64
import json
import secrets

from aiohttp import web

from config import OVERPAY_WEBHOOK_PASSWORD, OVERPAY_WEBHOOK_USERNAME
from core.webhook_abuse import (
    get_webhook_client_ip,
    is_webhook_ip_blocked,
)
from database import async_session_maker, get_payment_by_payment_id
from handlers.payments.overpay.service import get_overpay_order
from logger import logger
from services.payments.pipeline import (
    ParsedPayment,
    process_cancelled_payment,
    process_success_payment,
)


_PROVIDER = "overpay"

_STATUS_SUCCESS = {"charged", "approved", "settled", "completed", "success", "successful"}
_STATUS_REFUNDED = {"refunded"}
_STATUS_CHARGEBACK = {"chargeback"}
_STATUS_FAILED = {"declined", "rejected", "reversed", "error", "expired", "cancelled", "failed"}


def _webhook_auth_configured() -> bool:
    return bool((OVERPAY_WEBHOOK_USERNAME or "").strip()) or bool((OVERPAY_WEBHOOK_PASSWORD or "").strip())


def _verify_basic_auth(request: web.Request) -> bool:
    if not _webhook_auth_configured():
        return True

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False

    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
        return (
            secrets.compare_digest(username, OVERPAY_WEBHOOK_USERNAME)
            and secrets.compare_digest(password, OVERPAY_WEBHOOK_PASSWORD)
        )
    except Exception:
        return False


def _parse_tg_id_from_tx(merchant_tx_id: str) -> int | None:
    if not merchant_tx_id:
        return None
    parts = merchant_tx_id.split("_")
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except (ValueError, TypeError):
        return None


async def overpay_webhook(request: web.Request):
    try:
        ip = get_webhook_client_ip(request)
        if await is_webhook_ip_blocked(ip):
            return web.Response(status=429)

        if not _verify_basic_auth(request):
            logger.warning(f"[Overpay] Webhook: неверный Basic Auth от {ip}")
            return web.Response(status=401, text="unauthorized")

        raw_body = await request.read()
        try:
            data = json.loads(raw_body or b"{}")
        except Exception as e:
            logger.error(f"[Overpay] Невалидный JSON в webhook: {e}")
            return web.Response(status=400, text="bad json")

        logger.info(f"[Overpay] webhook: {json.dumps(data, ensure_ascii=False)}")

        order_id = str(data.get("id") or "")
        status = str(data.get("status") or "").strip().lower()
        merchant_tx_id = str(data.get("merchantTransactionId") or "")

        if not order_id:
            logger.error(f"[Overpay] Пустой id в webhook: {data}")
            return web.Response(status=400, text="missing id")

        async with async_session_maker() as lookup_session:
            pending = await get_payment_by_payment_id(lookup_session, order_id)

        tg_id: int | None = None
        rub_amount: float = 0.0
        if pending:
            try:
                rub_amount = float(pending.get("amount") or 0.0)
            except (TypeError, ValueError):
                rub_amount = 0.0
            if pending.get("tg_id") is not None:
                try:
                    tg_id = int(pending.get("tg_id"))
                except (TypeError, ValueError):
                    tg_id = None
        if tg_id is None:
            tg_id = _parse_tg_id_from_tx(merchant_tx_id)

        metadata_patch = {
            "provider": _PROVIDER,
            "overpay_order_id": order_id,
            "overpay_merchant_tx_id": merchant_tx_id or None,
            "overpay_status": status,
        }

        if status in _STATUS_SUCCESS:
            if tg_id is None:
                logger.error(f"[Overpay] Не удалось определить tg_id для order_id={order_id}")
                return web.Response(status=400, text="unknown payment")

            order = None
            if not _webhook_auth_configured():
                order = await get_overpay_order(order_id)
                if order is None:
                    logger.error(f"[Overpay] Не удалось подтвердить заказ {order_id} через API, повтор позже")
                    return web.Response(status=500, text="verification failed")

                confirmed_status = str(order.get("status") or "").strip().lower()
                if confirmed_status not in _STATUS_SUCCESS:
                    logger.warning(
                        f"[Overpay] Статус заказа {order_id} по API = '{confirmed_status}', зачисление отменено"
                    )
                    return web.Response(status=200, text="OK")

            if rub_amount <= 0:
                if order is None:
                    order = await get_overpay_order(order_id)
                if order is not None:
                    try:
                        rub_amount = float(order.get("amount") or 0)
                    except (TypeError, ValueError):
                        rub_amount = 0.0
                if rub_amount <= 0:
                    logger.error(f"[Overpay] Не удалось определить сумму зачисления для order_id={order_id}")
                    return web.Response(status=400, text="invalid amount")

            parsed = ParsedPayment(
                payment_id=order_id,
                tg_id=int(tg_id),
                amount=float(rub_amount),
                currency="RUB",
                metadata=metadata_patch,
            )
            result = await process_success_payment(_PROVIDER, parsed, metadata_patch=metadata_patch)
            if not result.ok:
                logger.error(f"[Overpay] Pipeline вернул ошибку: {result.error}, order_id={order_id}")
                return web.Response(status=500, text="pipeline error")

            logger.info(
                f"[Overpay] Платеж обработан: tg_id={tg_id}, amount={rub_amount:.2f} ₽, order_id={order_id}, "
                f"already_processed={result.already_processed}"
            )
            return web.Response(status=200, text="OK")

        if status in _STATUS_REFUNDED or status in _STATUS_CHARGEBACK:
            new_status = "refunded" if status in _STATUS_REFUNDED else "chargebacked"
            parsed = ParsedPayment(
                payment_id=order_id,
                tg_id=int(tg_id) if tg_id is not None else None,
                amount=float(rub_amount),
                currency="RUB",
                metadata=metadata_patch,
            )
            await process_cancelled_payment(_PROVIDER, parsed, new_status=new_status)
            logger.warning(f"[Overpay] Транзакция {status}: order_id={order_id}")
            return web.Response(status=200, text="OK")

        if status in _STATUS_FAILED:
            parsed = ParsedPayment(
                payment_id=order_id,
                tg_id=int(tg_id) if tg_id is not None else None,
                amount=float(rub_amount),
                currency="RUB",
                metadata=metadata_patch,
            )
            await process_cancelled_payment(_PROVIDER, parsed, new_status="failed")
            logger.info(f"[Overpay] Транзакция не состоялась ({status}): order_id={order_id}")
            return web.Response(status=200, text="OK")

        logger.info(f"[Overpay] Промежуточный статус '{status}' для order_id={order_id}, игнор")
        return web.Response(status=200, text="OK")
    except Exception as e:
        logger.error(f"[Overpay] Ошибка в webhook: {e}", exc_info=True)
        return web.Response(status=500, text="error")
