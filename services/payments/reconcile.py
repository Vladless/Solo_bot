import asyncio
import time

from database import async_session_maker, get_payment_from_db_by_payment_id
from database.access.resolution import TelegramId, UserId
from logger import logger
from services.payments.kassa2328.service import reconcile_payment as reconcile_kassa2328_payment
from services.payments.owner_refs import parse_payment_owner
from services.payments.pipeline import ParsedPayment, process_cancelled_payment, process_success_payment


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
    """Сверяет незавершённый платёж с провайдером."""
    payment_id = str(payment.get("payment_id") or "").strip()
    provider = str(payment.get("payment_system") or "").strip().lower()
    if not payment_id or _throttled(payment_id):
        return ""

    if provider == "2328":
        status = str(await reconcile_kassa2328_payment(payment_id) or "").lower()
        if status in {"paid", "overpaid"}:
            return "success"
        return "canceled" if status in {"cancel", "canceled", "cancelled", "refund_paid"} else ""

    if provider in {"yookassa_autopay", "yookassa_autopay_web"}:
        from services.payments.yookassa_autopay.service import reconcile_payment

        result = await reconcile_payment(payment_id)
        if not result.ok:
            return ""
        async with async_session_maker() as session:
            current = await get_payment_from_db_by_payment_id(session, payment_id)
        status = str((current or {}).get("status") or "").lower()
        return "success" if status == "success" else "canceled" if status in {"canceled", "cancelled"} else ""

    if provider not in {"yookassa", "yookassa_sbp"}:
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
            result = await process_cancelled_payment("yookassa", ParsedPayment(payment_id, None, 0))
            return "canceled" if result.ok else ""
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
        if payment.get("user_id") is not None:
            tg_id = UserId(payment["user_id"])
        elif isinstance(metadata, dict) and "billing_user_id" in metadata:
            tg_id = parse_payment_owner(f"u{metadata['billing_user_id']}")
        else:
            raw_tg = metadata.get("user_id") if isinstance(metadata, dict) else None
            tg_id = TelegramId(raw_tg) if raw_tg is not None else None
    except (TypeError, ValueError):
        tg_id = None
    if tg_id is None:
        owner = payment.get("tg_id")
        try:
            tg_id = TelegramId(owner) if owner is not None else None
        except (TypeError, ValueError):
            tg_id = None

    parsed = ParsedPayment(
        payment_id=payment_id,
        tg_id=tg_id,
        amount=amount,
        currency=currency,
        metadata={"yookassa_invoice": metadata.get("yookassa_invoice")} if isinstance(metadata, dict) else None,
    )
    logger.info(f"[Reconcile] Вебхук не дошёл — платёж {payment_id} подтверждён напрямую у YooKassa, провожу пайплайн")
    result = await process_success_payment("yookassa", parsed)
    return "success" if result.ok else ""
