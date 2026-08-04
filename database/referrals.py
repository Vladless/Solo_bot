from sqlalchemy import and_, desc, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import BUTTONS_CONFIG
from database.access.resolution import resolve_user_optional
from database.models import Referral
from logger import logger
from settings.config import CHECK_REFERRAL_REWARD_ISSUED, REFERRAL_BONUS_PERCENTAGES


async def add_referral(session: AsyncSession, referred_legacy: int, referrer_legacy: int):
    ru = await resolve_user_optional(session, referred_legacy)
    rf = await resolve_user_optional(session, referrer_legacy)
    if ru is None or rf is None:
        return
    if ru.id == rf.id:
        logger.warning(f"⚠️ Попытка самореферала: {referred_legacy}")
        return

    stmt = insert(Referral).values(
        referred_user_id=ru.id,
        referrer_user_id=rf.id,
        referred_tg_id=ru.tg_id,
        referrer_tg_id=rf.tg_id,
    )
    await session.execute(stmt)
    logger.info(f"✅ Добавлена реферальная связь: {ru.id} → {rf.id}")


async def get_referral_by_referred_id(session: AsyncSession, referred_legacy: int) -> dict | None:
    ru = await resolve_user_optional(session, referred_legacy)
    if ru is None:
        return None
    stmt = select(Referral).where(Referral.referred_user_id == ru.id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return dict(row.__dict__) if row else None


async def get_total_referrals(session: AsyncSession, referrer_legacy: int) -> int:
    ru = await resolve_user_optional(session, referrer_legacy)
    if ru is None:
        return 0
    stmt = select(func.count()).select_from(Referral).where(Referral.referrer_user_id == ru.id)
    result = await session.execute(stmt)
    return result.scalar()


async def get_active_referrals(session: AsyncSession, referrer_legacy: int) -> int:
    ru = await resolve_user_optional(session, referrer_legacy)
    if ru is None:
        return 0
    stmt = (
        select(func.count())
        .select_from(Referral)
        .where(
            and_(
                Referral.referrer_user_id == ru.id,
                Referral.reward_issued.is_(True),
            )
        )
    )
    result = await session.execute(stmt)
    return result.scalar()


async def mark_referral_reward_issued(session: AsyncSession, referred_legacy: int):
    ru = await resolve_user_optional(session, referred_legacy)
    if ru is None:
        return
    await session.execute(update(Referral).where(Referral.referred_user_id == ru.id).values(reward_issued=True))


async def get_total_referral_bonus(session: AsyncSession, referrer_legacy: int, max_levels: int) -> float:
    referral_enabled = bool(BUTTONS_CONFIG.get("REFERRAL_BUTTON_ENABLED", True))
    if not referral_enabled:
        logger.debug("Реферальная программа отключена, бонусы не начисляются")
        return 0.0

    ru = await resolve_user_optional(session, referrer_legacy)
    if ru is None:
        return 0.0
    uid = ru.id

    if CHECK_REFERRAL_REWARD_ISSUED:
        bonus_cte = """
            WITH RECURSIVE
            referral_levels AS (
                SELECT 
                    referred_user_id, 
                    referrer_user_id, 
                    1 AS level
                FROM referrals 
                WHERE referrer_user_id = :user_id AND reward_issued = TRUE
                
                UNION
                
                SELECT 
                    r.referred_user_id, 
                    r.referrer_user_id, 
                    rl.level + 1
                FROM referrals r
                JOIN referral_levels rl ON r.referrer_user_id = rl.referred_user_id
                WHERE rl.level < :max_levels AND r.reward_issued = TRUE
            ),
            earliest_payments AS (
                SELECT DISTINCT ON (user_id) user_id, amount, created_at
                FROM payments
                WHERE status = 'success' 
                  AND payment_system NOT IN ('coupon', 'admin', 'referral')
                ORDER BY user_id, created_at
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
            JOIN earliest_payments ep ON rl.referred_user_id = ep.user_id
            WHERE rl.level <= :max_levels
        """
        )
    else:
        bonus_cte = """
            WITH RECURSIVE
            referral_levels AS (
                SELECT 
                    referred_user_id, 
                    referrer_user_id, 
                    1 AS level
                FROM referrals 
                WHERE referrer_user_id = :user_id
                
                UNION
                
                SELECT 
                    r.referred_user_id, 
                    r.referrer_user_id, 
                    rl.level + 1
                FROM referrals r
                JOIN referral_levels rl ON r.referrer_user_id = rl.referred_user_id
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
            JOIN payments p ON rl.referred_user_id = p.user_id
            WHERE p.status = 'success' 
              AND p.payment_system NOT IN ('coupon', 'admin', 'referral')
              AND rl.level <= :max_levels
        """
        )

    result = await session.execute(
        text(bonus_query),
        {"user_id": uid, "max_levels": max_levels},
    )
    total_bonus_raw = result.scalar()
    total_bonus = round(float(total_bonus_raw or 0), 2)

    logger.debug(f"Получена общая сумма бонусов от рефералов: {total_bonus}")
    return total_bonus


async def get_referrals_by_level(session: AsyncSession, referrer_legacy: int, max_levels: int) -> dict:
    ru = await resolve_user_optional(session, referrer_legacy)
    if ru is None:
        return {}
    query = """
        WITH RECURSIVE referral_levels AS (
            SELECT referred_user_id, referrer_user_id, 1 AS level 
            FROM referrals 
            WHERE referrer_user_id = :referrer_user_id
            UNION
            SELECT r.referred_user_id, r.referrer_user_id, rl.level + 1
            FROM referrals r
            JOIN referral_levels rl ON r.referrer_user_id = rl.referred_user_id
            WHERE rl.level < :max_levels
        )
        SELECT level, 
               COUNT(*) AS level_count, 
               COUNT(CASE WHEN reward_issued THEN 1 END) AS active_level_count
        FROM referral_levels rl
        JOIN referrals r ON rl.referred_user_id = r.referred_user_id
        GROUP BY level
        ORDER BY level
    """
    result = await session.execute(
        text(query),
        {"referrer_user_id": ru.id, "max_levels": max_levels},
    )
    return {
        row["level"]: {
            "total": row["level_count"],
            "active": row["active_level_count"],
        }
        for row in result.mappings()
    }


async def get_referral_stats(session: AsyncSession, referrer_legacy: int):
    logger.info(f"[ReferralStats] Получение статистики для пользователя {referrer_legacy}")

    total_referrals = await get_total_referrals(session, referrer_legacy)
    active_referrals = await get_active_referrals(session, referrer_legacy)
    max_levels = len(REFERRAL_BONUS_PERCENTAGES)
    referrals_by_level = await get_referrals_by_level(session, referrer_legacy, max_levels)
    total_referral_bonus = await get_total_referral_bonus(session, referrer_legacy, max_levels)

    return {
        "total_referrals": total_referrals,
        "active_referrals": active_referrals,
        "referrals_by_level": referrals_by_level,
        "total_referral_bonus": total_referral_bonus,
    }


async def get_user_referral_count(session: AsyncSession, legacy: int) -> int:
    ru = await resolve_user_optional(session, legacy)
    if ru is None:
        return 0
    result = await session.execute(select(func.count()).select_from(Referral).where(Referral.referrer_user_id == ru.id))
    return result.scalar_one() or 0


async def get_referral_position(session: AsyncSession, referral_count: int) -> int:
    subq = (
        select(Referral.referrer_user_id)
        .group_by(Referral.referrer_user_id)
        .having(func.count() > referral_count)
        .subquery()
    )
    query = select(func.count()).select_from(subq)
    result = await session.execute(query)
    count = result.scalar() or 0
    return count + 1


async def get_top_referrals(session: AsyncSession, limit: int = 5):
    query = (
        select(Referral.referrer_user_id, func.count().label("referral_count"))
        .group_by(Referral.referrer_user_id)
        .order_by(desc("referral_count"))
        .limit(limit)
    )
    result = await session.execute(query)
    return [{"referrer_user_id": row.referrer_user_id, "referral_count": row.referral_count} for row in result.all()]
