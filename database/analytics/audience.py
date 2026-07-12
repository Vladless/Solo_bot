from sqlalchemy import func, select

from database.models import Referral, User

from .base import StatsCtx, users_series


async def _by_source(ctx: StatsCtx) -> list[dict]:
    """Новые пользователи за период по UTM-источнику."""
    rows = (
        await ctx.session.execute(
            select(User.source_code, func.count())
            .where(User.created_at >= ctx.since)
            .group_by(User.source_code)
            .order_by(func.count().desc())
            .limit(12)
        )
    ).all()
    return [{"source": (sc or "—"), "count": int(c or 0)} for sc, c in rows]


async def _referrals(ctx: StatsCtx) -> dict:
    """Реферальная воронка: всего / с выплаченной наградой."""
    total = int(await ctx.scalar(select(func.count()).select_from(Referral)))
    rewarded = int(await ctx.scalar(select(func.count()).select_from(Referral).where(Referral.reward_issued.is_(True))))
    return {"total": total, "rewarded": rewarded, "reward_rate_pct": round(100.0 * rewarded / total, 1) if total else 0.0}


async def audience(ctx: StatsCtx) -> dict:
    """Пользователи/привлечение: всего, новые, триал, ряд, источники, рефералы, баланс."""
    total_users = int(await ctx.scalar(select(func.count()).select_from(User)))
    new_users = int(await ctx.scalar(select(func.count()).select_from(User).where(User.created_at >= ctx.since)))
    trial_users = int(await ctx.scalar(select(func.count()).select_from(User).where(User.trial > 0)))
    balance = await ctx.scalar(select(func.coalesce(func.sum(User.balance), 0)))
    return {
        "total_users": total_users,
        "new_users": new_users,
        "trial_users": trial_users,
        "series": await users_series(ctx),
        "by_source": await _by_source(ctx),
        "referrals": await _referrals(ctx),
        "balance_liability": round(float(balance), 2),
    }
