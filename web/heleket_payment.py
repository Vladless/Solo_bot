import base64
import hashlib
import json

from decimal import ROUND_HALF_UP, Decimal

import aiohttp

from aiohttp import web
from sqlalchemy import select

from config import HELEKET_API_KEY
from database import Payment, async_session_maker, update_balance, update_payment_status
from handlers.payments.currency_rates import to_rub
from handlers.payments.utils import send_payment_success_notification
from logger import logger


processed_payments = set()


async def heleket_payment_webhook(request: web.Request):
    try:
        data = await request.json()
        logger.info(f"Heleket webhook received from {request.remote}")
        logger.info(f"Heleket webhook data: {data}")

        signature = data.get("sign", "")
        if not verify_heleket_webhook_signature(data, signature):
            logger.error("Heleket: Invalid signature")
            return web.Response(status=400, text="Invalid signature")

        uuid = data.get("uuid")
        order_id = data.get("order_id")
        payment_status = data.get("status")
        amount_raw = data.get("amount")
        currency = str(data.get("currency") or "").upper() or None
        payment_amount_raw = data.get("payment_amount")
        to_currency = str(data.get("to_currency") or "").upper() or None
        additional_data = data.get("additional_data", "")

        logger.info(
            f"Heleket payment: uuid={uuid}, order_id={order_id}, status={payment_status}, "
            f"amount={amount_raw}, payment_amount={payment_amount_raw}, currency={currency}, to_currency={to_currency}"
        )

        if payment_status != "paid":
            logger.info(f"Heleket: Payment not completed, status={payment_status}")
            return web.Response(status=200, text="OK")

        if not uuid:
            logger.error("Heleket: Missing uuid")
            return web.Response(status=400, text="Missing required fields")

        if uuid in processed_payments:
            logger.warning(f"Heleket: Duplicate payment uuid={uuid}")
            return web.Response(status=200, text="OK")
        if order_id and order_id in processed_payments:
            logger.warning(f"Heleket: Duplicate payment order_id={order_id}")
            return web.Response(status=200, text="OK")

        tg_id = None
        rub_amount: Decimal | None = None

        try:
            if additional_data and "tg_id:" in additional_data:
                parts = [p.strip() for p in additional_data.split(",")]
                for part in parts:
                    if part.startswith("tg_id:"):
                        tg_id = int(part.split("tg_id:")[1])
                    elif part.startswith("rub_amount:"):
                        val = part.split("rub_amount:")[1]
                        rub_amount = Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if tg_id is None and order_id and "_" in order_id:
                tg_id = int(order_id.split("_")[1])
        except (ValueError, IndexError) as e:
            logger.error(f"Heleket: Error extracting tg_id or rub_amount: {e}")
            return web.Response(status=400, text="Invalid user ID or amount format")

        paid_ccy = None
        original_amount_dec: Decimal | None = None

        if currency and amount_raw is not None:
            try:
                original_amount_dec = Decimal(str(amount_raw))
                paid_ccy = currency
            except Exception:
                original_amount_dec = None

        if original_amount_dec is None and to_currency and payment_amount_raw is not None:
            try:
                original_amount_dec = Decimal(str(payment_amount_raw))
                paid_ccy = to_currency
            except Exception:
                original_amount_dec = None

        if rub_amount is None:
            amt_str = str(amount_raw or "").strip()
            if amt_str and currency:
                try:
                    amt_dec = Decimal(amt_str)
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                        rub_amount = await to_rub(amt_dec, currency, session=s)
                except Exception as e:
                    logger.error(f"Heleket: RUB conversion failed ({currency}→RUB) for amount={amt_str}: {e}")
                    rub_amount = None

        if rub_amount is None and payment_amount_raw and to_currency:
            try:
                amt_dec = Decimal(str(payment_amount_raw))
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                    rub_amount = await to_rub(amt_dec, to_currency, session=s)
            except Exception as e:
                logger.error(
                    f"Heleket: RUB conversion failed ({to_currency}→RUB) for payment_amount={payment_amount_raw}: {e}"
                )
                rub_amount = None

        if rub_amount is None:
            logger.error("Heleket: Could not determine rub_amount")
            return web.Response(status=400, text="Cannot determine amount")

        if tg_id is None:
            logger.error("Heleket: Cannot extract tg_id from data")
            return web.Response(status=400, text="Cannot extract user ID")

        meta_patch = {
            "provider": "HELEKET",
            "heleket_raw": data,
            "provider_uuid": uuid,
            "provider_order_id": order_id,
        }
        if paid_ccy and original_amount_dec is not None:
            meta_patch["paid_invoice_amount"] = float(original_amount_dec)
            meta_patch["paid_invoice_currency"] = paid_ccy
            meta_patch["fx"] = {"base": paid_ccy, "rub_equivalent": str(rub_amount)}

        async with async_session_maker() as session:
            internal_payment = None
            if order_id:
                res = await session.execute(select(Payment).where(Payment.payment_id == str(order_id)).limit(1))
                internal_payment = res.scalar_one_or_none()

            if internal_payment:
                internal_payment.status = "success"
                if order_id:
                    internal_payment.payment_id = str(order_id)
                if paid_ccy:
                    internal_payment.currency = paid_ccy
                if original_amount_dec is not None:
                    internal_payment.original_amount = float(original_amount_dec)
                current_md = getattr(internal_payment, "metadata_", None)
                if isinstance(current_md, dict):
                    current_md.update(meta_patch)
                    internal_payment.metadata_ = current_md
                else:
                    internal_payment.metadata_ = meta_patch
                await session.commit()
            else:
                ok = await update_payment_status(
                    session=session,
                    internal_id=0,
                    new_status="success",
                    payment_id=str(order_id) if order_id else None,
                    metadata_patch=meta_patch,
                )
                if not ok:
                    logger.error("Heleket: Failed to update payment status for unknown internal payment")

            await update_balance(session, tg_id, float(rub_amount))
            await send_payment_success_notification(tg_id, float(rub_amount), session)

        processed_payments.add(uuid)
        if order_id:
            processed_payments.add(order_id)

        logger.info(f"Heleket: Payment processed for user {tg_id}, amount {rub_amount} RUB, uuid={uuid}")
        return web.Response(status=200, text="OK")

    except Exception as e:
        logger.error(f"Heleket webhook error: {e}")
        return web.Response(status=500, text="Internal server error")


def verify_heleket_webhook_signature(data: dict, signature: str) -> bool:
    try:
        data_without_sign = {k: v for k, v in data.items() if k != "sign"}
        json_data = json.dumps(data_without_sign, separators=(",", ":"), ensure_ascii=False)
        base64_data = base64.b64encode(json_data.encode("utf-8")).decode("utf-8")
        sign_string = base64_data + HELEKET_API_KEY
        expected_signature = hashlib.md5(sign_string.encode("utf-8")).hexdigest()

        result = signature.upper() == expected_signature.upper()
        if not result:
            logger.error("Heleket webhook signature mismatch")
            logger.error(f"Expected: {expected_signature}, Got: {signature}")
            logger.error(f"Base64 data: {base64_data}")
            logger.error(f"Sign string: {sign_string}")
        return result
    except Exception as e:
        logger.error(f"Heleket signature verification error: {e}")
        return False
