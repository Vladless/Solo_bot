import asyncio
import time

from logger import logger
from services.payments.pipeline import ParsedPayment, process_success_payment


_RECONCILE_MIN_INTERVAL_SEC = 5.0
_last_attempts: dict[str, float] = {}


def _throttled(payment_id: str) -> bool:
    now = time.monotonic()
    last = _last_attempts.get(payment_id)
    if last is not None and now - last < _RECONCILE_MIN_INTERVAL_SEC:
        return True
    _last_attempts[payment_id] = now
    if len(_last_attempts) > 500:
        cutoff = now - 3600
        for k in [k for k, v in _last_attempts.items() if v < cutoff]:
            _last_attempts.pop(k, None)
    return False


async def reconcile_pending_payment(payment: dict) -> str:
    """Сверяет pending-платёж напрямую с провайдером (страховка от потерянного вебхука).

    Возвращает 'success' — платёж подтверждён и проведён через success-пайплайн
    (идемпотентно); 'canceled' — отменён/истёк у провайдера; '' — пока без изменений.
    """
    payment_id = str(payment.get("payment_id") or "").strip()
    provider = str(payment.get("payment_system") or "").strip().lower()
    if not payment_id or _throttled(payment_id):
        return ""

    if provider != "yookassa":
        return ""

    try:
        from yookassa import Payment as YooPayment

        obj = await asyncio.to_thread(YooPayment.find_one, payment_id)
    except Exception as e:
        logger.warning(f"[Reconcile] YooKassa find_one({payment_id}) не удался: {e}")
        return ""

    status = str(getattr(obj, "status", "") or "").lower()
    paid = bool(getattr(obj, "paid", False))
    if status != "succeeded" or not paid:
        if status in {"canceled", "cancelled"}:
            logger.info(f"[Reconcile] Платёж {payment_id} отменён на стороне YooKassa")
            return "canceled"
        return ""

    amount_obj = getattr(obj, "amount", None)
    try:
        amount = float(getattr(amount_obj, "value", 0) or 0)
    except (TypeError, ValueError):
        amount = 0.0
    currency = str(getattr(amount_obj, "currency", "RUB") or "RUB")

    metadata = getattr(obj, "metadata", None) or {}
    tg_id = None
    try:
        raw_uid = metadata.get("user_id") if isinstance(metadata, dict) else None
        tg_id = int(raw_uid) if raw_uid is not None else None
    except (TypeError, ValueError):
        tg_id = None
    if tg_id is None:
        owner = payment.get("user_id") if payment.get("user_id") is not None else payment.get("tg_id")
        try:
            tg_id = int(owner) if owner is not None else None
        except (TypeError, ValueError):
            tg_id = None

    parsed = ParsedPayment(
        payment_id=payment_id,
        tg_id=tg_id,
        amount=amount,
        currency=currency,
    )
    logger.info(f"[Reconcile] Вебхук не дошёл — платёж {payment_id} подтверждён напрямую у YooKassa, провожу пайплайн")
    result = await process_success_payment("yookassa", parsed)
    return "success" if result.ok else ""
