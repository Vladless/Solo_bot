from datetime import datetime

from sqlalchemy import case, delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

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
    try:
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
        await session.commit()
        logger.info(f"[Coupon] ✅ Купон {code} успешно создан.")
        return True
    except SQLAlchemyError as e:
        await session.rollback()
        logger.error(f"[Coupon] ❌ Ошибка при создании купона {code}: {e}")
        return False


async def get_coupon_by_code(session: AsyncSession, code: str) -> Coupon | None:
    stmt = select(Coupon).where(Coupon.code == code)
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
    await session.commit()
    logger.info(f"🗑 Купон {code} удалён вместе с его использованиями")
    return True


async def create_coupon_usage(session: AsyncSession, coupon_id: int, user_id: int):
    try:
        stmt = insert(CouponUsage).values(coupon_id=coupon_id, user_id=user_id, used_at=datetime.utcnow())
        await session.execute(stmt)
        await session.commit()
        logger.info(f"✅ Купон {coupon_id} использован пользователем {user_id}")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при сохранении использования купона: {e}")
        await session.rollback()
        raise


async def check_coupon_usage(session: AsyncSession, coupon_id: int, user_id: int) -> bool:
    stmt = select(CouponUsage).where(CouponUsage.coupon_id == coupon_id, CouponUsage.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def update_coupon_usage_count(session: AsyncSession, coupon_id: int):
    try:
        await session.execute(
            update(Coupon)
            .where(Coupon.id == coupon_id)
            .values(
                usage_count=Coupon.usage_count + 1,
                is_used=case((Coupon.usage_count + 1 >= Coupon.usage_limit, True), else_=False),
            )
        )
        await session.commit()
        logger.info(f"🔁 Обновлён счётчик купона {coupon_id}")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при обновлении купона {coupon_id}: {e}")
        await session.rollback()
        raise


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
