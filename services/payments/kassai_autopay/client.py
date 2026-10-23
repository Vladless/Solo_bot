from __future__ import annotations

import re

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import aiohttp

from core.settings.kassai_autopay_config import KASSAI_AUTOPAY_CONFIG
from logger import logger
from services.payments.kassai.merchant import (
    merchant_request_nonce,
    sign_merchant_request as sign_request,
)
from settings.config import KASSAI_API_KEY, KASSAI_SECRET_KEY, KASSAI_SHOP_ID


def money(value: object, *, positive: bool = True) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Некорректная сумма платежа") from exc
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError("Некорректная сумма платежа")
    if amount > Decimal("1000000") or amount != amount.quantize(Decimal("0.01")):
        raise ValueError("Сумма платежа должна содержать не более двух знаков после запятой")
    return amount.quantize(Decimal("0.01"))


@dataclass(frozen=True)
class APIResult:
    data: dict | None = None
    unknown: bool = False
    error: str | None = None


def _rejection_fields(data: dict) -> str:
    """Возвращает имена ошибочных полей без значений запроса."""
    errors = data.get("errors")
    if not isinstance(errors, dict):
        return "unknown"
    allowed = {"shopId", "nonce", "signature", "paymentId", "i", "email", "ip", "amount", "currency", "tel"}
    fields = sorted(allowed.intersection(errors))
    return ",".join(fields) or "unknown"


def _rejection_reason(data: dict, outgoing: dict) -> str:
    """Очищает описание отказа от реквизитов и данных плательщика."""
    message = next(
        (
            value
            for key in ("message", "description", "error")
            if isinstance(value := data.get(key), str) and value.strip()
        ),
        None,
    )
    if message is None:
        return "unknown"
    private_fields = {"signature", "token", "us_kgt", "email", "ip", "tel", "paymentId"}
    private_values = {str(value) for key, value in outgoing.items() if key in private_fields and value is not None}
    private_values.update(str(value) for value in (KASSAI_API_KEY, KASSAI_SECRET_KEY) if value)
    for value in sorted(private_values, key=len, reverse=True):
        if value:
            message = message.replace(value, "[redacted]")
    message = re.sub(
        r"(?i)\b(token|us_kgt|signature|api[_ -]?key|secret(?:[_ -]?key)?)"
        r"([\"']?\s*[:=]\s*[\"']?)[^\s,;\"'&}\]]+",
        r"\1\2[redacted]",
        message,
    )
    message = re.sub(r"[^\s@]+@[^\s@]+", "[redacted-email]", message)
    message = " ".join("".join(char if char.isprintable() else " " for char in message).split())
    return message[:500] or "unknown"


class KassaIClient:
    base_url = "https://api.fk.life/v1"

    async def request(
        self, path: str, payload: dict, *, before_send: Callable[[], Awaitable[bool]] | None = None
    ) -> APIResult:
        if not KASSAI_SHOP_ID or not KASSAI_API_KEY:
            return APIResult(error="Не настроены реквизиты KassaI")
        async with merchant_request_nonce(KASSAI_SHOP_ID) as nonce:
            if before_send is not None and not await before_send():
                return APIResult(error="Отправка платежа отменена до обращения к KassaI")
            outgoing = {**payload, "shopId": KASSAI_SHOP_ID, "nonce": nonce}
            outgoing["signature"] = sign_request(outgoing, KASSAI_API_KEY)
            timeout = aiohttp.ClientTimeout(total=max(1, int(KASSAI_AUTOPAY_CONFIG.get("API_TIMEOUT", 30))))
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(f"{self.base_url}/{path}", json=outgoing) as response:
                        try:
                            data = await response.json()
                        except (ValueError, aiohttp.ContentTypeError):
                            return APIResult(unknown=True, error="KassaI вернула некорректный ответ")
                        if response.status >= 500 or response.status in {408, 429}:
                            return APIResult(unknown=True, error="KassaI временно недоступна")
                        if not isinstance(data, dict):
                            return APIResult(unknown=True, error="KassaI вернула некорректный ответ")
                        if response.status >= 400 or data.get("type") != "success":
                            code = data.get("code")
                            if isinstance(code, bool) or not isinstance(code, int):
                                code = "unknown"
                            logger.warning(
                                "KassaI merchant API: запрос отклонён, path={}, HTTP={}, code={}, fields={}, reason={}",
                                path,
                                response.status,
                                code,
                                _rejection_fields(data),
                                _rejection_reason(data, outgoing),
                            )
                            return APIResult(error="KassaI отклонила запрос")
                        return APIResult(data=data)
            except (aiohttp.ClientError, TimeoutError):
                return APIResult(unknown=True, error="Результат запроса KassaI пока неизвестен")

    async def create_payment(
        self, payload: dict, *, before_send: Callable[[], Awaitable[bool]] | None = None
    ) -> APIResult:
        return await self.request("orders/create", payload, before_send=before_send)

    async def get_payment(self, payment_id: str) -> APIResult:
        result = await self.request("orders", {"paymentId": payment_id})
        if result.data is None:
            return result
        orders = result.data.get("orders")
        if not isinstance(orders, list):
            return APIResult(unknown=True, error="KassaI не подтвердила состояние заказа")
        matching = [row for row in orders if isinstance(row, dict) and str(row.get("merchant_order_id")) == payment_id]
        if len(matching) != 1:
            return APIResult(unknown=True, error="Заказ пока не найден однозначно; повторное списание заблокировано")
        return APIResult(data=matching[0])

    async def delete_method(self, token: str) -> APIResult:
        return await self.request("orders/kg_recurrent_delete", {"token": token})


client = KassaIClient()
