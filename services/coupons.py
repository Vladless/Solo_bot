from __future__ import annotations

from typing import TYPE_CHECKING

from database.coupons import (
    apply_percent_coupon,
    check_coupon_usage,
    claim_coupon_slot,
    clear_coupon_hold,
    create_coupon_usage,
    get_coupon_by_code_ci,
    get_coupon_hold,
    release_coupon_slot,
    set_coupon_hold,
)
from database.keys import count_active_keys_for_user
from database.models import Coupon
from database.payments import add_payment, count_successful_payments
from database.users import get_balance, update_balance
from logger import logger

from .errors import LimitExceededError, NotFoundError, ServiceError, ValidationError


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_percent_coupon(
    session: AsyncSession,
    billing_user_id: int,
    base_price_rub: int,
    coupon_code: str | None,
) -> tuple[int, int, int | None, str | None]:
    """Возвращает цену со скидкой по введённому коду либо по закреплённому за клиентом купону."""
    normalized = (coupon_code or "").strip()
    if not normalized:
        return await _resolve_held_coupon(session, billing_user_id, base_price_rub)

    coupon = await get_coupon_by_code_ci(session, normalized)
    if coupon is None:
        raise NotFoundError("Купон не найден")

    await _check_percent_coupon(session, coupon, billing_user_id)

    discounted_price, discount_rub = apply_percent_coupon(int(base_price_rub), coupon)
    if int(discount_rub) <= 0:
        raise ValidationError("Купон не применим к текущей сумме")

    return int(discounted_price), int(discount_rub), int(coupon.id), str(coupon.code or normalized)


async def resolve_percent_coupon_soft(
    session: AsyncSession,
    billing_user_id: int,
    base_price_rub: int,
    coupon_code: str | None,
) -> tuple[int, int, int | None, str | None]:
    """Возвращает цену со скидкой, а при непригодном купоне — базовую цену."""
    try:
        return await resolve_percent_coupon(
            session=session,
            billing_user_id=billing_user_id,
            base_price_rub=base_price_rub,
            coupon_code=coupon_code,
        )
    except ServiceError as e:
        logger.info("[Coupon] купон '{}' не применён ({}): {}", coupon_code, e.code, e.message)
        return int(base_price_rub), 0, None, None


async def _resolve_held_coupon(
    session: AsyncSession,
    billing_user_id: int,
    base_price_rub: int,
) -> tuple[int, int, int | None, str | None]:
    """Применяет закреплённый за клиентом купон и снимает закрепление, если купон непригоден."""
    coupon = await get_coupon_hold(session, int(billing_user_id))
    if coupon is None:
        return int(base_price_rub), 0, None, None

    try:
        await _check_percent_coupon(session, coupon, billing_user_id)
    except ServiceError:
        await clear_coupon_hold(session, int(billing_user_id))
        return int(base_price_rub), 0, None, None

    discounted_price, discount_rub = apply_percent_coupon(int(base_price_rub), coupon)
    if int(discount_rub) <= 0:
        return int(base_price_rub), 0, None, None

    return int(discounted_price), int(discount_rub), int(coupon.id), str(coupon.code or "")


async def _check_percent_coupon(session: AsyncSession, coupon: Coupon, billing_user_id: int) -> None:
    """Проверяет пригодность процентного купона для клиента."""
    _check_coupon_limits(coupon)

    if await check_coupon_usage(session, int(coupon.id), int(billing_user_id)):
        raise LimitExceededError("Вы уже использовали этот купон")

    if getattr(coupon, "percent", None) is None:
        raise ValidationError("Поддерживаются только процентные купоны")

    if bool(getattr(coupon, "new_users_only", False)):
        await _check_new_user(session, billing_user_id)


class CouponHoldResult:
    __slots__ = ("coupon_code", "percent", "max_discount_amount", "min_order_amount")

    def __init__(
        self, coupon_code: str, percent: int, max_discount_amount: int | None, min_order_amount: int | None
    ) -> None:
        self.coupon_code = coupon_code
        self.percent = percent
        self.max_discount_amount = max_discount_amount
        self.min_order_amount = min_order_amount


async def hold_percent_coupon(session: AsyncSession, user_id: int, code: str) -> CouponHoldResult:
    """Закрепляет процентный купон за клиентом до ближайшей оплаты."""
    normalized = (code or "").strip()
    if not normalized:
        raise ValidationError("Введите код купона")

    coupon = await get_coupon_by_code_ci(session, normalized)
    if coupon is None:
        raise NotFoundError("Купон не найден")

    await _check_percent_coupon(session, coupon, user_id)

    if not await set_coupon_hold(session, int(user_id), int(coupon.id)):
        raise ValidationError("Не удалось сохранить скидку")

    return CouponHoldResult(
        coupon_code=str(coupon.code or normalized),
        percent=int(getattr(coupon, "percent", 0) or 0),
        max_discount_amount=(
            int(coupon.max_discount_amount) if getattr(coupon, "max_discount_amount", None) is not None else None
        ),
        min_order_amount=(
            int(coupon.min_order_amount) if getattr(coupon, "min_order_amount", None) is not None else None
        ),
    )


async def peek_percent_hold(session: AsyncSession, user_id: int) -> CouponHoldResult | None:
    """Возвращает закреплённую за клиентом скидку и снимает непригодное закрепление."""
    coupon = await get_coupon_hold(session, int(user_id))
    if coupon is None:
        return None

    try:
        await _check_percent_coupon(session, coupon, user_id)
    except ServiceError:
        await clear_coupon_hold(session, int(user_id))
        return None

    return CouponHoldResult(
        coupon_code=str(coupon.code or ""),
        percent=int(getattr(coupon, "percent", 0) or 0),
        max_discount_amount=(
            int(coupon.max_discount_amount) if getattr(coupon, "max_discount_amount", None) is not None else None
        ),
        min_order_amount=(
            int(coupon.min_order_amount) if getattr(coupon, "min_order_amount", None) is not None else None
        ),
    )


async def drop_percent_coupon(session: AsyncSession, user_id: int) -> None:
    """Снимает закрепление скидки у клиента."""
    await clear_coupon_hold(session, int(user_id))


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
    """Активирует купон с фиксированной суммой и зачисляет её на баланс."""
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
        user_id=int(user_id),
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


async def is_new_user(session: AsyncSession, user_id: int) -> bool:
    """Проверяет, что у клиента нет ни успешных платежей, ни активных ключей."""
    payments_count = await count_successful_payments(session, int(user_id))
    keys_count = await count_active_keys_for_user(session, int(user_id))
    return payments_count == 0 and keys_count == 0


async def _check_new_user(session: AsyncSession, user_id: int) -> None:
    if not await is_new_user(session, user_id):
        raise ValidationError("Этот купон доступен только для новых пользователей")
