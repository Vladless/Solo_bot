from __future__ import annotations

from typing import TYPE_CHECKING

from database.coupons import (
    apply_percent_coupon,
    check_coupon_usage,
    claim_coupon_slot,
    create_coupon_usage,
    get_coupon_by_code_ci,
    release_coupon_slot,
)
from database.keys import count_active_keys_for_user
from database.models import Coupon
from database.payments import add_payment, count_successful_payments
from database.users import get_balance, update_balance

from .errors import LimitExceededError, NotFoundError, ValidationError


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_percent_coupon(
    session: AsyncSession,
    billing_user_id: int,
    base_price_rub: int,
    coupon_code: str | None,
) -> tuple[int, int, int | None, str | None]:
    """Применяет процентный купон к цене.

    Returns: (final_price, discount, coupon_id, coupon_code)
    Raises: NotFoundError, LimitExceededError, ValidationError
    """
    normalized = (coupon_code or "").strip()
    if not normalized:
        return int(base_price_rub), 0, None, None

    coupon = await get_coupon_by_code_ci(session, normalized)
    if coupon is None:
        raise NotFoundError("Купон не найден")

    _check_coupon_limits(coupon)

    if await check_coupon_usage(session, int(coupon.id), int(billing_user_id)):
        raise LimitExceededError("Вы уже использовали этот купон")

    percent = getattr(coupon, "percent", None)
    if percent is None:
        raise ValidationError("Поддерживаются только процентные купоны")

    if bool(getattr(coupon, "new_users_only", False)):
        await _check_new_user(session, billing_user_id)

    discounted_price, discount_rub = apply_percent_coupon(int(base_price_rub), coupon)
    if int(discount_rub) <= 0:
        raise ValidationError("Купон не применим к текущей сумме")

    return int(discounted_price), int(discount_rub), int(coupon.id), str(coupon.code or normalized)


class CouponApplyResult:
    __slots__ = ("coupon_code", "amount", "balance")

    def __init__(self, coupon_code: str, amount: int, balance: float) -> None:
        self.coupon_code = coupon_code
        self.amount = amount
        self.balance = balance


async def apply_fixed_coupon(
    session: AsyncSession,
    user_id: int,
    tg_id: int | None,
    code: str,
) -> CouponApplyResult:
    """Активирует купон с фиксированной суммой — зачисляет на баланс.

    Raises: NotFoundError, LimitExceededError, ValidationError
    """
    normalized = code.strip()
    if not normalized:
        raise ValidationError("Введите код купона")

    coupon = await get_coupon_by_code_ci(session, normalized)
    if coupon is None:
        raise NotFoundError("Купон не найден")

    _check_coupon_limits(coupon)

    if await check_coupon_usage(session, int(coupon.id), int(user_id)):
        raise LimitExceededError("Вы уже использовали этот купон")

    percent = getattr(coupon, "percent", None)
    if percent is not None:
        raise ValidationError("Этот купон применяется при оплате тарифа")

    days = int(getattr(coupon, "days", 0) or 0)
    if days > 0:
        raise ValidationError("Купон на продление применяйте через Telegram-бота")

    amount = int(getattr(coupon, "amount", 0) or 0)
    if amount <= 0:
        raise ValidationError("Купон недействителен")

    if bool(getattr(coupon, "new_users_only", False)):
        await _check_new_user(session, user_id)

    if not await claim_coupon_slot(session, int(coupon.id)):
        raise ValidationError("Лимит активаций купона исчерпан")
    if not await create_coupon_usage(session, int(coupon.id), int(user_id)):
        await release_coupon_slot(session, int(coupon.id))
        raise ValidationError("Купон уже активирован")
    await update_balance(session, int(user_id), float(amount))
    await add_payment(
        session=session,
        legacy_user_ref=int(user_id),
        amount=float(amount),
        payment_system="coupon",
        status="success",
        currency="RUB",
    )

    balance = float(await get_balance(session, int(user_id)))

    return CouponApplyResult(
        coupon_code=str(coupon.code or normalized),
        amount=amount,
        balance=balance,
    )


def _check_coupon_limits(coupon: Coupon) -> None:
    usage_limit = int(getattr(coupon, "usage_limit", 0) or 0)
    usage_count = int(getattr(coupon, "usage_count", 0) or 0)
    is_used = bool(getattr(coupon, "is_used", False))
    if usage_limit > 0 and (is_used or usage_count >= usage_limit):
        raise LimitExceededError("Лимит активаций купона исчерпан")


async def _check_new_user(session: AsyncSession, user_id: int) -> None:
    payments_count = await count_successful_payments(session, int(user_id))
    keys_count = await count_active_keys_for_user(session, int(user_id))
    if payments_count > 0 or keys_count > 0:
        raise ValidationError("Этот купон доступен только для новых пользователей")
