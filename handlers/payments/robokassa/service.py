import hashlib
import json
import uuid

from decimal import ROUND_DOWN, Decimal
from urllib.parse import quote_plus, urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from config import ROBOKASSA_LOGIN, ROBOKASSA_PASSWORD1, ROBOKASSA_PASSWORD2, ROBOKASSA_TEST_MODE
from database import add_payment


def _build_receipt(amount: float, sno: str = "usn_income") -> dict:
    return {
        "items": [
            {
                "name": "Пополнение баланса",
                "quantity": 1,
                "sum": float(amount),
                "payment_method": "full_payment",
                "payment_object": "payment",
                "tax": "none",
            }
        ],
        "sno": sno,
    }


def _format_amount(amount: float | int) -> str:
    s = str(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
    return s.rstrip("0").rstrip(".") if "." in s else s


def generate_payment_link(amount: int | float, inv_id: int, description: str, tg_id: int) -> tuple[str, str]:
    out_sum = _format_amount(amount)
    receipt_json = json.dumps(_build_receipt(amount), ensure_ascii=False, separators=(",", ":"))
    receipt_enc = quote_plus(receipt_json, safe="")
    pid = str(uuid.uuid4())
    shp = {"Shp_id": str(tg_id), "Shp_pid": pid}
    base = f"{ROBOKASSA_LOGIN}:{out_sum}:{inv_id}:{receipt_enc}:{ROBOKASSA_PASSWORD1}"
    for k in sorted(shp.keys(), key=str.lower):
        base += f":{k}={shp[k]}"
    signature = hashlib.md5(base.encode("utf-8")).hexdigest().upper()
    query = {
        "MrchLogin": ROBOKASSA_LOGIN,
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": description,
        "Receipt": receipt_enc,
        "SignatureValue": signature,
        **shp,
    }
    if ROBOKASSA_TEST_MODE:
        query["IsTest"] = 1
    return "https://auth.robokassa.ru/Merchant/Index.aspx?" + urlencode(query), pid


async def create_and_store_robokassa_payment(
    session: AsyncSession, tg_id: int, amount: int | float, description: str, inv_id: int = 0
) -> tuple[str, str]:
    url, pid = generate_payment_link(amount, inv_id, description, tg_id)
    await add_payment(
        session=session,
        tg_id=tg_id,
        amount=float(amount),
        payment_system="robokassa",
        status="pending",
        currency="RUB",
        payment_id=pid,
        metadata=None,
    )
    return url, pid


def check_payment_signature(params) -> bool:
    out_sum = params.get("OutSum") or params.get("out_summ") or params.get("outsumm")
    inv_id = params.get("InvId") or params.get("inv_id") or params.get("invid")
    received_sig = (params.get("SignatureValue") or params.get("signaturevalue") or "").upper()
    if not out_sum or not inv_id or not received_sig:
        return False
    shp_items = [(k, params[k]) for k in params.keys() if k.lower().startswith("shp_")]
    shp_items.sort(key=lambda kv: kv[0].lower())
    shp_suffix = "".join(f":{k}={v}" for k, v in shp_items)
    base = f"{out_sum}:{inv_id}:{ROBOKASSA_PASSWORD2}{shp_suffix}"
    expected_sig = hashlib.md5(base.encode("utf-8")).hexdigest().upper()
    return received_sig == expected_sig
