import json

from aiohttp import web

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
from settings.config import PLATEGA_API_SECRET, PLATEGA_MERCHANT_ID


_PROVIDER = "platega"

_STATUS_CONFIRMED = "CONFIRMED"
_STATUS_CANCELED = "CANCELED"
_STATUS_CHARGEBACKED = "CHARGEBACKED"


def _validate_credentials(request: web.Request) -> bool:
    merchant_id = (PLATEGA_MERCHANT_ID or "").strip()
    secret = (PLATEGA_API_SECRET or "").strip()
    if not merchant_id or not secret:
        return False

    received_mid = (request.headers.get("X-MerchantId") or "").strip()
    received_secret = (request.headers.get("X-Secret") or "").strip()

    return received_mid == merchant_id and received_secret == secret


async def platega_webhook(request: web.Request):
    try:
        ip = get_webhook_client_ip(request)
        if await is_webhook_ip_blocked(ip):
            return web.Response(status=429)

        raw_body = await request.read()
        try:
            data = json.loads(raw_body or b"{}")
        except Exception as e:
            logger.error(f"[Platega] Невалидный JSON в webhook: {e}")
            return web.Response(status=400, text="bad json")

        if not _validate_credentials(request):
            logger.warning("[Platega] Webhook 403: невалидные X-MerchantId / X-Secret")
            await record_webhook_signature_failure(ip)
            return web.Response(status=403, text="forbidden")

        logger.info(f"[Platega] webhook: {json.dumps(data, ensure_ascii=False)}")

        status = str(data.get("status") or "").strip().upper()
        transaction_id = str(data.get("id") or "")
        webhook_amount = data.get("amount")
        webhook_currency = str(data.get("currency") or "RUB").upper()
        payload_value = str(data.get("payload") or "")

        if not transaction_id:
            logger.error(f"[Platega] Пустой id транзакции в webhook: {data}")
            return web.Response(status=400, text="missing id")

        async with async_session_maker() as lookup_session:
            pending = await get_payment_by_payment_id(lookup_session, transaction_id)

        tg_id: int | None = None
        rub_amount: float = 0.0

        if pending:
            try:
                rub_amount = float(pending.get("amount") or 0.0)
            except (TypeError, ValueError):
                rub_amount = 0.0
            try:
                if pending.get("tg_id") is not None:
                    tg_id = int(pending.get("tg_id"))
            except (TypeError, ValueError):
                tg_id = None

        metadata_patch: dict = {
            "provider": _PROVIDER,
            "platega_transaction_id": transaction_id,
            "platega_currency": webhook_currency,
            "platega_amount": webhook_amount,
            "platega_payment_method": data.get("paymentMethod"),
            "platega_status": status,
            "platega_payload": payload_value or None,
        }

        if status == _STATUS_CONFIRMED:
            if tg_id is None:
                logger.error(
                    f"[Platega] Не удалось определить tg_id для transaction={transaction_id}; "
                    f"pending запись не найдена."
                )
                return web.Response(status=400, text="unknown payment")

            if rub_amount <= 0:
                if webhook_currency == "RUB":
                    try:
                        rub_amount = float(webhook_amount or 0)
                    except (TypeError, ValueError):
                        rub_amount = 0.0
                if rub_amount <= 0:
                    logger.error(
                        f"[Platega] Не удалось определить RUB-сумму для зачисления: transaction={transaction_id}"
                    )
                    return web.Response(status=400, text="invalid amount")

            update_currency: str | None = None
            update_original_amount: float | None = None
            if webhook_currency != "RUB":
                update_currency = webhook_currency
                try:
                    update_original_amount = float(webhook_amount) if webhook_amount is not None else None
                except (TypeError, ValueError):
                    update_original_amount = None

            parsed = ParsedPayment(
                payment_id=transaction_id,
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
                logger.error(f"[Platega] Pipeline вернул ошибку: {result.error}, transaction={transaction_id}")
                return web.Response(status=500, text="pipeline error")

            logger.info(
                f"[Platega] Платёж обработан: tg_id={tg_id}, amount={rub_amount:.2f} ₽, "
                f"transaction={transaction_id}, "
                f"already_processed={result.already_processed}"
            )
            return web.Response(status=200, text="OK")

        if status == _STATUS_CANCELED:
            parsed_amount = float(rub_amount) if rub_amount > 0 else 0.0
            if parsed_amount <= 0:
                try:
                    parsed_amount = float(webhook_amount or 0)
                except (TypeError, ValueError):
                    parsed_amount = 0.0

            parsed = ParsedPayment(
                payment_id=transaction_id,
                tg_id=int(tg_id) if tg_id is not None else None,
                amount=parsed_amount,
                currency=webhook_currency,
                metadata=metadata_patch,
            )
            await process_cancelled_payment(_PROVIDER, parsed, new_status="cancelled")

            logger.info(f"[Platega] Платёж отменён: transaction={transaction_id}")
            return web.Response(status=200, text="OK")

        if status == _STATUS_CHARGEBACKED:
            parsed_amount = float(rub_amount) if rub_amount > 0 else 0.0
            if parsed_amount <= 0:
                try:
                    parsed_amount = float(webhook_amount or 0)
                except (TypeError, ValueError):
                    parsed_amount = 0.0

            parsed = ParsedPayment(
                payment_id=transaction_id,
                tg_id=int(tg_id) if tg_id is not None else None,
                amount=parsed_amount,
                currency=webhook_currency,
                metadata=metadata_patch,
            )
            await process_cancelled_payment(_PROVIDER, parsed, new_status="chargebacked")

            logger.warning(
                f"[Platega] CHARGEBACK: transaction={transaction_id}, amount={webhook_amount} {webhook_currency}"
            )
            return web.Response(status=200, text="OK")

        logger.warning(f"[Platega] Неизвестный статус транзакции: {status}, transaction={transaction_id}")
        return web.Response(status=200, text="OK")

    except Exception as e:
        logger.error(f"[Platega] Ошибка в webhook: {e}", exc_info=True)
        return web.Response(status=500, text="error")
