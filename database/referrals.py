from sqlalchemy import and_, desc, func, insert, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config import CHECK_REFERRAL_REWARD_ISSUED, REFERRAL_BONUS_PERCENTAGES
from database.models import Referral
from logger import logger


async def add_referral(session: AsyncSession, referred_tg_id: int, referrer_tg_id: int):
    try:
        if referred_tg_id == referrer_tg_id:
            logger.warning(f"⚠️ Попытка самореферала: {referred_tg_id}")
            return

        stmt = insert(Referral).values(
            referred_tg_id=referred_tg_id, referrer_tg_id=referrer_tg_id
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(
            f"✅ Добавлена реферальная связь: {referred_tg_id} → {referrer_tg_id}"
        )
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при добавлении реферала: {e}")
        await session.rollback()
        raise


async def get_referral_by_referred_id(
    session: AsyncSession, referred_tg_id: int
) -> dict | None:
    stmt = select(Referral).where(Referral.referred_tg_id == referred_tg_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return dict(row.__dict__) if row else None


async def get_total_referrals(session: AsyncSession, referrer_tg_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(Referral)
        .where(Referral.referrer_tg_id == referrer_tg_id)
    )
    result = await session.execute(stmt)
    return result.scalar()


async def get_active_referrals(session: AsyncSession, referrer_tg_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(Referral)
        .where(
            and_(
                Referral.referrer_tg_id == referrer_tg_id,
                Referral.reward_issued is True,
            )
        )
    )
    result = await session.execute(stmt)
    return result.scalar()


async def mark_referral_reward_issued(session: AsyncSession, referred_tg_id: int):
    await session.execute(
        update(Referral)
        .where(Referral.referred_tg_id == referred_tg_id)
        .values(reward_issued=True)
    )
    await session.commit()


async def get_total_referral_bonus(
    session: AsyncSession, referrer_tg_id: int, max_levels: int
) -> float:
    if CHECK_REFERRAL_REWARD_ISSUED:
        bonus_cte = """
            WITH RECURSIVE
            referral_levels AS (
                SELECT 
                    referred_tg_id, 
                    referrer_tg_id, 
                    1 AS level
                FROM referrals 
                WHERE referrer_tg_id = :tg_id AND reward_issued = TRUE
                
                UNION
                
                SELECT 
                    r.referred_tg_id, 
                    r.referrer_tg_id, 
                    rl.level + 1
                FROM referrals r
                JOIN referral_levels rl ON r.referrer_tg_id = rl.referred_tg_id
                WHERE rl.level < :max_levels AND r.reward_issued = TRUE
            ),
            earliest_payments AS (
                SELECT DISTINCT ON (tg_id) tg_id, amount, created_at
                FROM payments
                WHERE status = 'success'
                ORDER BY tg_id, created_at
            )
        """
        bonus_query = (
            bonus_cte
            + f"""
            SELECT 
                COALESCE(SUM(
                    CASE
                        {
                " ".join([
                    f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]} * ep.amount"
                    if isinstance(REFERRAL_BONUS_PERCENTAGES[level], float)
                    else f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]}"
                    for level in REFERRAL_BONUS_PERCENTAGES
                ])
            }
                        ELSE 0 
                    END
                ), 0) AS total_bonus
            FROM referral_levels rl
            JOIN earliest_payments ep ON rl.referred_tg_id = ep.tg_id
            WHERE rl.level <= :max_levels
        """
        )
    else:
        bonus_cte = """
            WITH RECURSIVE
            referral_levels AS (
                SELECT 
                    referred_tg_id, 
                    referrer_tg_id, 
                    1 AS level
                FROM referrals 
                WHERE referrer_tg_id = :tg_id
                
                UNION
                
                SELECT 
                    r.referred_tg_id, 
                    r.referrer_tg_id, 
                    rl.level + 1
                FROM referrals r
                JOIN referral_levels rl ON r.referrer_tg_id = rl.referred_tg_id
                WHERE rl.level < :max_levels
            )
        """
        bonus_query = (
            bonus_cte
            + f"""
            SELECT 
                COALESCE(SUM(
                    CASE
                        {
                " ".join([
                    f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]} * p.amount"
                    if isinstance(REFERRAL_BONUS_PERCENTAGES[level], float)
                    else f"WHEN rl.level = {level} THEN {REFERRAL_BONUS_PERCENTAGES[level]}"
                    for level in REFERRAL_BONUS_PERCENTAGES
                ])
            }
                        ELSE 0 
                    END
                ), 0) AS total_bonus
            FROM referral_levels rl
            JOIN payments p ON rl.referred_tg_id = p.tg_id
            WHERE p.status = 'success' AND rl.level <= :max_levels
        """
        )

    result = await session.execute(
        text(bonus_query), {"tg_id": referrer_tg_id, "max_levels": max_levels}
    )
    total_bonus_raw = result.scalar()
    total_bonus = round(float(total_bonus_raw or 0), 2)

    logger.debug(f"Получена общая сумма бонусов от рефералов: {total_bonus}")
    return total_bonus


async def get_referrals_by_level(
    session: AsyncSession, referrer_tg_id: int, max_levels: int
) -> dict:
    query = """
        WITH RECURSIVE referral_levels AS (
            SELECT referred_tg_id, referrer_tg_id, 1 AS level 
            FROM referrals 
            WHERE referrer_tg_id = :referrer_tg_id
            UNION
            SELECT r.referred_tg_id, r.referrer_tg_id, rl.level + 1
            FROM referrals r
            JOIN referral_levels rl ON r.referrer_tg_id = rl.referred_tg_id
            WHERE rl.level < :max_levels
        )
        SELECT level, 
               COUNT(*) AS level_count, 
               COUNT(CASE WHEN reward_issued THEN 1 END) AS active_level_count
        FROM referral_levels rl
        JOIN referrals r ON rl.referred_tg_id = r.referred_tg_id
        GROUP BY level
        ORDER BY level
    """
    result = await session.execute(
        text(query), {"referrer_tg_id": referrer_tg_id, "max_levels": max_levels}
    )
    return {
        row["level"]: {
            "total": row["level_count"],
            "active": row["active_level_count"],
        }
        for row in result.mappings()
    }


async def get_referral_stats(session: AsyncSession, referrer_tg_id: int):
    try:
        logger.info(
            f"[ReferralStats] Получение статистики для пользователя {referrer_tg_id}"
        )

        total_referrals = await get_total_referrals(session, referrer_tg_id)
        active_referrals = await get_active_referrals(session, referrer_tg_id)
        max_levels = len(REFERRAL_BONUS_PERCENTAGES)
        referrals_by_level = await get_referrals_by_level(
            session, referrer_tg_id, max_levels
        )
        total_referral_bonus = await get_total_referral_bonus(
            session, referrer_tg_id, max_levels
        )

        return {
            "total_referrals": total_referrals,
            "active_referrals": active_referrals,
            "referrals_by_level": referrals_by_level,
            "total_referral_bonus": total_referral_bonus,
        }

    except Exception as e:
        logger.error(
            f"[ReferralStats] Ошибка при получении статистики для пользователя {referrer_tg_id}: {e}"
        )
        raise


async def get_user_referral_count(session: AsyncSession, tg_id: int) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(Referral)
        .where(Referral.referrer_tg_id == tg_id)
    )
    return result.scalar_one() or 0


async def get_referral_position(session: AsyncSession, referral_count: int) -> int:
    subq = (
        select(Referral.referrer_tg_id)
        .group_by(Referral.referrer_tg_id)
        .having(func.count() > referral_count)
        .subquery()
    )
    query = select(func.count()).select_from(subq)
    result = await session.execute(query)
    count = result.scalar() or 0
    return count + 1


async def get_top_referrals(session: AsyncSession, limit: int = 5):
    query = (
        select(Referral.referrer_tg_id, func.count().label("referral_count"))
        .group_by(Referral.referrer_tg_id)
        .order_by(desc("referral_count"))
        .limit(limit)
    )
    result = await session.execute(query)
    return [
        {"referrer_tg_id": row.referrer_tg_id, "referral_count": row.referral_count}
        for row in result.all()
    ]
