from datetime import datetime

from sqlalchemy import case, delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Coupon, CouponUsage
from logger import logger


async def create_coupon(
    session: AsyncSession, code: str, amount: int, usage_limit: int, days: int = None
) -> bool:
    try:
        exists = await session.scalar(select(Coupon.id).where(Coupon.code == code))
        if exists:
            logger.warning(f"[Coupon] ⚠️ Купон с кодом {code} уже существует.")
            return False

        await session.execute(
            insert(Coupon).values(
                code=code,
                amount=amount,
                usage_limit=usage_limit,
                usage_count=0,
                is_used=False,
                days=days,
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


async def get_all_coupons(
    session: AsyncSession, page: int = 1, per_page: int = 10
) -> dict:
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
        stmt = insert(CouponUsage).values(
            coupon_id=coupon_id, user_id=user_id, used_at=datetime.utcnow()
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"✅ Купон {coupon_id} использован пользователем {user_id}")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при сохранении использования купона: {e}")
        await session.rollback()


async def check_coupon_usage(
    session: AsyncSession, coupon_id: int, user_id: int
) -> bool:
    stmt = select(CouponUsage).where(
        CouponUsage.coupon_id == coupon_id, CouponUsage.user_id == user_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def update_coupon_usage_count(session: AsyncSession, coupon_id: int):
    try:
        await session.execute(
            update(Coupon)
            .where(Coupon.id == coupon_id)
            .values(
                usage_count=Coupon.usage_count + 1,
                is_used=case(
                    (Coupon.usage_count + 1 >= Coupon.usage_limit, True), else_=False
                ),
            )
        )
        await session.commit()
        logger.info(f"🔁 Обновлён счётчик купона {coupon_id}")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при обновлении купона {coupon_id}: {e}")
        await session.rollback()
