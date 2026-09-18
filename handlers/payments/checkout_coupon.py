from math import ceil
from typing import Any

from database.coupons import get_coupon_by_code_ci
from database.temporary_data import create_temporary_data
from database.users import get_balance
from services.coupons import resolve_percent_coupon
from settings.texts import FASTFLOW_COUPON_APPLIED_TEMPLATE


PRICE_KEYS = ("selected_price_rub", "cost", "agreed_extra_price")


def payload_base_price(payload: dict) -> int | None:
    """Возвращает цену корзины по первому заполненному ценовому ключу."""
    for key in PRICE_KEYS:
        raw = payload.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


async def apply_checkout_coupon(
    session: Any,
    user_ref: int,
    temp_key: str,
    payload: dict,
    required_amount: int | None,
    coupon_code: str | None = None,
) -> tuple[dict, int | None, str]:
    """Применяет процентный купон к корзине и возвращает её вместе с суммой доплаты."""
    if coupon_code is None and payload.get("applied_coupon_code"):
        return payload, required_amount, ""

    base_price = payload_base_price(payload)
    if base_price is None:
        return payload, required_amount, ""

    new_price, discount, coupon_id, code = await resolve_percent_coupon(
        session=session,
        billing_user_id=user_ref,
        base_price_rub=base_price,
        coupon_code=coupon_code,
    )
    if coupon_id is None or discount <= 0:
        return payload, required_amount, ""

    balance = await get_balance(session, user_ref)
    required = int(max(0, ceil(float(new_price) - float(balance))))

    updated = dict(payload)
    updated["required_amount"] = required
    updated["coupon_id"] = int(coupon_id)
    updated["applied_coupon_code"] = str(code or "")
    for key in PRICE_KEYS:
        if key in updated:
            updated[key] = int(new_price)

    await create_temporary_data(session, user_ref, temp_key, updated)

    coupon = await get_coupon_by_code_ci(session, str(code or ""))
    note = FASTFLOW_COUPON_APPLIED_TEMPLATE.format(
        code=str(code or ""),
        percent=int(getattr(coupon, "percent", 0) or 0),
        old_price=int(base_price),
        discount=int(discount),
        new_price=int(new_price),
    )
    return updated, required, note
