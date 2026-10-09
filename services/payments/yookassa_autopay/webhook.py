from __future__ import annotations

from aiohttp import web

from database import (
    async_session_maker,
    yookassa_autopay as dal,
)
from services.payments.pipeline import PipelineResult

from .client import client, money
from .service import _save_provider_result, process_verified_refund, reconcile_payment


async def dispatch_if_known(payload: dict) -> PipelineResult | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("object"), dict):
        return None
    obj = payload["object"]
    event = str(payload.get("event") or "")
    payment_id = obj.get("payment_id") if event == "refund.succeeded" else obj.get("id")
    if not isinstance(payment_id, str):
        return None
    async with async_session_maker() as session:
        attempt = await dal.get_attempt_by_payment_id(session, payment_id)
        legacy = await dal.get_legacy_payment(session, payment_id)
        if attempt is None and event.startswith("payment."):
            attempt_id = (obj.get("metadata") or {}).get("autopay_attempt_id")
            if isinstance(attempt_id, str):
                candidate = await dal.get_attempt(session, attempt_id)
                if candidate and candidate.payment_id in {None, payment_id}:
                    attempt = candidate
    if attempt is None and legacy is None:
        return None
    if event == "refund.succeeded":
        refund_id = obj.get("id")
        if not isinstance(refund_id, str):
            return PipelineResult(ok=False, error="Не указан идентификатор возврата")
        verified = await client.get_refund(refund_id)
        data = verified.data
        if not data:
            return PipelineResult(ok=False, error=verified.error)
        if data.get("id") != refund_id or data.get("payment_id") != payment_id:
            return PipelineResult(ok=False, error="Возврат не соответствует платежу")
        if data.get("status") != "succeeded":
            return PipelineResult(ok=True)
        try:
            amount = float(money(data["amount"]["value"]))
            if data["amount"]["currency"] != "RUB":
                raise ValueError("Неверная валюта возврата")
        except (KeyError, TypeError, ValueError):
            return PipelineResult(ok=False, error="Неверная сумма возврата")
        return await process_verified_refund(payment_id, refund_id, amount)
    if event not in {"payment.succeeded", "payment.canceled", "payment.waiting_for_capture"}:
        return PipelineResult(ok=True)
    if attempt is not None and attempt.payment_id is None:
        verified = await client.get_payment(payment_id)
        if verified.data is None:
            return PipelineResult(ok=False, error=verified.error)
        return await _save_provider_result(attempt.id, verified.data)
    return await reconcile_payment(payment_id)


async def process_webhook(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (ValueError, TypeError):
        return web.Response(status=400)
    if not isinstance(payload, dict):
        return web.Response(status=400)
    result = await dispatch_if_known(payload)
    if result is None:
        from services.payments.yookassa.webhook import yookassa_webhook

        return await yookassa_webhook(request)
    return web.Response(status=200 if result.ok else 500)
