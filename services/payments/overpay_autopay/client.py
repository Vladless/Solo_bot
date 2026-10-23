from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation

from core.settings.overpay_autopay_config import OVERPAY_AUTOPAY_CONFIG
from services.payments.overpay.client import APIResult, request
from settings import texts


def money(value: object, *, positive: bool = True) -> Decimal:
    """Проверяет сумму платежа с точностью до копейки."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(texts.OVERPAY_AUTOPAY_INVALID_AMOUNT) from exc
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError(texts.OVERPAY_AUTOPAY_INVALID_AMOUNT)
    if amount > Decimal("1000000") or amount != amount.quantize(Decimal("0.01")):
        raise ValueError(texts.OVERPAY_AUTOPAY_AMOUNT_PRECISION)
    return amount.quantize(Decimal("0.01"))


class OverPayClient:
    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        params: dict | None = None,
        before_send: Callable[[], Awaitable[bool]] | None = None,
    ) -> APIResult:
        return await request(
            method,
            path,
            payload=payload,
            params=params,
            timeout_seconds=int(OVERPAY_AUTOPAY_CONFIG.get("API_TIMEOUT", 30)),
            before_send=before_send,
        )

    async def create_payment(
        self, payload: dict, *, before_send: Callable[[], Awaitable[bool]] | None = None
    ) -> APIResult:
        options = payload.get("options")
        if (
            payload.get("paymentMethods") != ["card"]
            or not isinstance(options, dict)
            or options.get("recurring") is not True
        ):
            return APIResult(error=texts.OVERPAY_AUTOPAY_RECURRING_CONSENT_REQUIRED)
        return await self.request("POST", "orders/preflight", payload=payload, before_send=before_send)

    async def rebill(
        self,
        source_order_id: str,
        payload: dict,
        *,
        before_send: Callable[[], Awaitable[bool]] | None = None,
    ) -> APIResult:
        order_id = str(source_order_id)
        if not order_id.isascii() or not order_id.isdecimal() or len(order_id) > 64:
            return APIResult(error=texts.OVERPAY_AUTOPAY_METHOD_INVALID)
        if set(payload) != {"amount", "location"}:
            return APIResult(error=texts.OVERPAY_AUTOPAY_REBILL_INVALID)
        location = payload.get("location")
        if not isinstance(location, dict) or not isinstance(location.get("ip"), str) or not location["ip"].strip():
            return APIResult(error=texts.OVERPAY_AUTOPAY_REBILL_IP_REQUIRED)
        try:
            amount = money(payload.get("amount"))
        except ValueError as exc:
            return APIResult(error=str(exc))
        outgoing = {"amount": f"{amount:.2f}", "location": {"ip": location["ip"]}}
        return await self.request("POST", f"orders/{order_id}/rebill", payload=outgoing, before_send=before_send)

    async def get_payment(self, payment_id: str, provider_order_id: str | None = None) -> APIResult:
        if provider_order_id is not None:
            order_id = str(provider_order_id)
            if not order_id.isascii() or not order_id.isdecimal() or len(order_id) > 64:
                return APIResult(unknown=True, error=texts.OVERPAY_AUTOPAY_ORDER_ID_INVALID)
            result = await self.request("GET", f"orders/{order_id}")
        else:
            result = await self.request("GET", "orders", params={"merchant_order_id": payment_id})
        if result.data is None:
            return result
        orders = result.data.get("orders")
        if not isinstance(orders, list):
            return APIResult(unknown=True, error=texts.OVERPAY_AUTOPAY_ORDER_STATUS_UNKNOWN)
        matching = [
            row
            for row in orders
            if isinstance(row, dict)
            and (
                str(row.get("id")) == str(provider_order_id)
                if provider_order_id is not None
                else row.get("merchant_order_id") == payment_id
            )
        ]
        if len(matching) != 1:
            return APIResult(unknown=True, error=texts.OVERPAY_AUTOPAY_ORDER_UNKNOWN)
        return APIResult(data=matching[0])


client = OverPayClient()
