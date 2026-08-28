from datetime import datetime

from sqlalchemy import and_, case, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import resolve_user_optional
from database.models import Coupon, CouponUsage
from logger import logger


async def create_coupon(
    session: AsyncSession,
    code: str,
    amount: int | None,
    usage_limit: int,
    days: int | None = None,
    new_users_only: bool = False,
    percent: int | None = None,
    max_discount_amount: int | None = None,
    min_order_amount: int | None = None,
) -> bool:
    exists = await session.scalar(select(Coupon.id).where(Coupon.code == code))
    if exists:
        logger.warning(f"[Coupon] ⚠️ Купон с кодом {code} уже существует.")
        return False

    if percent is not None:
        try:
            percent_value = int(percent)
        except (TypeError, ValueError):
            logger.warning(f"[Coupon] ⚠️ Некорректный процент для купона {code}.")
            return False

        if percent_value <= 0 or percent_value > 100:
            logger.warning(f"[Coupon] ⚠️ процент должен быть в диапазоне 1..100 для купона {code}.")
            return False

        if (amount or 0) > 0 or (days or 0) > 0:
            logger.warning(f"[Coupon] ⚠️ Купон {code} не может одновременно иметь percent и amount/days.")
            return False

    await session.execute(
        insert(Coupon).values(
            code=code,
            amount=int(amount) if amount is not None else 0,
            usage_limit=usage_limit,
            usage_count=0,
            is_used=False,
            days=days,
            new_users_only=new_users_only,
            percent=percent,
            max_discount_amount=max_discount_amount,
            min_order_amount=min_order_amount,
        )
    )
    logger.info(f"[Coupon] ✅ Купон {code} успешно создан.")
    return True


async def get_coupon_by_code(session: AsyncSession, code: str) -> Coupon | None:
    stmt = select(Coupon).where(Coupon.code == code)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_coupon_by_code_ci(session: AsyncSession, code: str) -> Coupon | None:
    normalized = str(code or "").strip()
    if not normalized:
        return None
    stmt = select(Coupon).where(func.lower(Coupon.code) == normalized.lower())
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_all_coupons(session: AsyncSession, page: int = 1, per_page: int = 10) -> dict:
    offset = (page - 1) * per_page

    stmt = select(Coupon).order_by(Coupon.id.desc()).offset(offset).limit(per_page)
    result = await session.execute(stmt)
    coupons = result.scalars().all()

    count_stmt = select(func.count()).select_from(Coupon)
    total = await session.scalar(count_stmt)
    pages = -(-total // per_page)

    return {
        "coupons": [c.to_dict() for c in coupons],
        "total": total,
        "pages": pages,
        "current_page": page,
    }


async def delete_coupon(session: AsyncSession, code: str) -> bool:
    result = await session.execute(select(Coupon).where(Coupon.code == code))
    coupon = result.scalar_one_or_none()

    if not coupon:
        logger.info(f"❌ Купон {code} не найден")
        return False

    await session.execute(delete(CouponUsage).where(CouponUsage.coupon_id == coupon.id))

    await session.delete(coupon)
    logger.info(f"🗑 Купон {code} удалён вместе с его использованиями")
    return True


async def _coupon_usage_billing_match(session: AsyncSession, legacy_user_ref: int):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is not None:
        opts = [CouponUsage.user_id == u.id]
        if u.tg_id is not None:
            opts.append(CouponUsage.tg_id == u.tg_id)
        return or_(*opts)
    return or_(CouponUsage.user_id == legacy_user_ref, CouponUsage.tg_id == legacy_user_ref)


async def create_coupon_usage(session: AsyncSession, coupon_id: int, user_id: int) -> bool:
    u = await resolve_user_optional(session, user_id)
    uid = u.id if u is not None else user_id
    stmt = (
        pg_insert(CouponUsage)
        .values(
            coupon_id=coupon_id,
            user_id=uid,
            tg_id=u.tg_id if u is not None else None,
            used_at=datetime.utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["coupon_id", "user_id"])
    )
    result = await session.execute(stmt)
    inserted = bool(result.rowcount)
    if inserted:
        logger.info(f"✅ Купон {coupon_id} использован пользователем {user_id}")
    return inserted


async def check_coupon_usage(session: AsyncSession, coupon_id: int, legacy_user_ref: int) -> bool:
    m = await _coupon_usage_billing_match(session, legacy_user_ref)
    stmt = select(CouponUsage).where(CouponUsage.coupon_id == coupon_id).where(m)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def has_any_coupon_usage(session: AsyncSession, legacy_user_ref: int) -> bool:
    m = await _coupon_usage_billing_match(session, legacy_user_ref)
    stmt = select(CouponUsage.coupon_id).where(m).limit(1)
    result = await session.execute(stmt)
    return result.first() is not None


async def claim_coupon_slot(session: AsyncSession, coupon_id: int) -> bool:
    """Занимает слот купона одним запросом. False — лимит уже исчерпан.

    Проверять лимит отдельным select нельзя: два клиента одновременно пройдут
    проверку последнего слота и оба получат бонус.
    """
    used = func.coalesce(Coupon.usage_count, 0)
    result = await session.execute(
        update(Coupon)
        .where(
            Coupon.id == coupon_id,
            or_(Coupon.usage_limit.is_(None), used < Coupon.usage_limit),
        )
        .values(
            usage_count=used + 1,
            is_used=case(
                (and_(Coupon.usage_limit.isnot(None), used + 1 >= Coupon.usage_limit), True),
                else_=False,
            ),
        )
    )
    claimed = bool(result.rowcount)
    if claimed:
        logger.info(f"🔁 Обновлён счётчик купона {coupon_id}")
    else:
        logger.info(f"⛔ Слот купона {coupon_id} не занят: лимит исчерпан")
    return claimed


async def release_coupon_slot(session: AsyncSession, coupon_id: int) -> None:
    """Возвращает занятый слот, если активация дальше не состоялась."""
    used = func.coalesce(Coupon.usage_count, 0)
    await session.execute(
        update(Coupon)
        .where(Coupon.id == coupon_id, used > 0)
        .values(
            usage_count=used - 1,
            is_used=case(
                (and_(Coupon.usage_limit.isnot(None), used - 1 >= Coupon.usage_limit), True),
                else_=False,
            ),
        )
    )


async def update_coupon_usage_count(session: AsyncSession, coupon_id: int) -> bool:
    return await claim_coupon_slot(session, coupon_id)


async def mark_coupon_used(session: AsyncSession, coupon_id: int, legacy_user_ref: int):
    u = await resolve_user_optional(session, legacy_user_ref)
    uid = u.id if u is not None else legacy_user_ref
    match = [CouponUsage.user_id == int(uid)]
    if u is not None and u.tg_id is not None:
        match.append(CouponUsage.tg_id == int(u.tg_id))
    existing = await session.execute(
        select(CouponUsage).where(
            CouponUsage.coupon_id == int(coupon_id),
            or_(*match),
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    await session.execute(
        insert(CouponUsage).values(
            coupon_id=coupon_id,
            user_id=uid,
            tg_id=u.tg_id if u is not None else None,
            used_at=datetime.utcnow(),
        )
    )
    await session.execute(
        update(Coupon)
        .where(Coupon.id == coupon_id)
        .values(
            usage_count=Coupon.usage_count + 1,
            is_used=case((Coupon.usage_count + 1 >= Coupon.usage_limit, True), else_=False),
        )
    )


def apply_percent_coupon(price_rub: int, coupon: Coupon) -> tuple[int, int]:
    percent = coupon.percent
    if percent is None:
        return price_rub, 0

    if coupon.min_order_amount is not None and price_rub < int(coupon.min_order_amount):
        return price_rub, 0

    discount = (price_rub * int(percent)) // 100

    if coupon.max_discount_amount is not None:
        discount = min(discount, int(coupon.max_discount_amount))

    final_price = price_rub - discount
    if final_price < 0:
        final_price = 0

    return final_price, discount
