from __future__ import annotations

import re

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import aiohttp

from core.settings.yookassa_autopay_config import YOOKASSA_AUTOPAY_CONFIG
from settings import texts
from settings.config import YOOKASSA_SECRET_KEY, YOOKASSA_SHOP_ID


_OBJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _merchant_credentials() -> tuple[str, str]:
    shop_id = str(YOOKASSA_AUTOPAY_CONFIG.get("SHOP_ID") or "").strip()
    secret_key = str(YOOKASSA_AUTOPAY_CONFIG.get("SECRET_KEY") or "").strip()
    if bool(shop_id) != bool(secret_key):
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_PARTIAL_CREDENTIALS)
    if not shop_id:
        shop_id = str(YOOKASSA_SHOP_ID or "").strip()
        secret_key = str(YOOKASSA_SECRET_KEY or "").strip()
    if not shop_id or not secret_key:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_CREDENTIALS_REQUIRED)
    return shop_id, secret_key


def money(value: object, *, positive: bool = True) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_INVALID_AMOUNT) from exc
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_INVALID_AMOUNT)
    if amount > Decimal("1000000") or amount != amount.quantize(Decimal("0.01")):
        raise ValueError(texts.YOOKASSA_AUTOPAY_SERVICE_INVALID_PRECISION)
    return amount.quantize(Decimal("0.01"))


@dataclass(frozen=True)
class APIResult:
    data: dict | None = None
    unknown: bool = False
    error: str | None = None


class YooKassaClient:
    base_url = "https://api.yookassa.ru/v3"

    async def request(
        self, method: str, path: str, *, payload: dict | None = None, idempotency_key: str | None = None
    ) -> APIResult:
        try:
            shop_id, secret_key = _merchant_credentials()
        except ValueError as exc:
            return APIResult(error=str(exc))
        headers = {"Idempotence-Key": idempotency_key} if idempotency_key else {}
        timeout = aiohttp.ClientTimeout(total=max(1, int(YOOKASSA_AUTOPAY_CONFIG.get("API_TIMEOUT", 30))))
        try:
            async with aiohttp.ClientSession(auth=aiohttp.BasicAuth(shop_id, secret_key)) as client:
                async with client.request(
                    method, f"{self.base_url}/{path}", json=payload, headers=headers, timeout=timeout
                ) as response:
                    try:
                        data = await response.json()
                    except (ValueError, aiohttp.ContentTypeError):
                        return APIResult(
                            unknown=True,
                            error=texts.YOOKASSA_AUTOPAY_SERVICE_INVALID_HTTP_RESPONSE.format(status=response.status),
                        )
                    if response.status >= 500 or response.status in {408, 429}:
                        return APIResult(
                            unknown=True,
                            error=texts.YOOKASSA_AUTOPAY_SERVICE_TEMPORARILY_UNAVAILABLE.format(status=response.status),
                        )
                    if response.status >= 400:
                        return APIResult(
                            error=texts.YOOKASSA_AUTOPAY_SERVICE_REQUEST_REJECTED.format(status=response.status)
                        )
                    if not isinstance(data, dict):
                        return APIResult(unknown=True, error=texts.YOOKASSA_AUTOPAY_SERVICE_INVALID_RESPONSE)
                    return APIResult(data=data)
        except (aiohttp.ClientError, TimeoutError):
            return APIResult(unknown=True, error=texts.YOOKASSA_AUTOPAY_SERVICE_REQUEST_UNKNOWN)

    async def create_payment(self, payload: dict, idempotency_key: str) -> APIResult:
        return await self.request("POST", "payments", payload=payload, idempotency_key=idempotency_key)

    async def get_payment(self, payment_id: str) -> APIResult:
        if not isinstance(payment_id, str) or not _OBJECT_ID.fullmatch(payment_id):
            return APIResult(error=texts.YOOKASSA_AUTOPAY_SERVICE_INVALID_PAYMENT_ID)
        return await self.request("GET", f"payments/{payment_id}")

    async def get_refund(self, refund_id: str) -> APIResult:
        if not isinstance(refund_id, str) or not _OBJECT_ID.fullmatch(refund_id):
            return APIResult(error=texts.YOOKASSA_AUTOPAY_SERVICE_INVALID_REFUND_ID)
        return await self.request("GET", f"refunds/{refund_id}")


client = YooKassaClient()
