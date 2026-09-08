from sqlalchemy import and_, func, select

from core.settings.bonus_config import resolve_daily_bonus_rules
from database.models import DailyBonusClaim, Key, Payment

from .base import INTERNAL_SYSTEMS, StatsCtx


async def _series(ctx: StatsCtx) -> list[dict]:
    """Выдачи и сумма по дням за период."""
    rows = (
        await ctx.session.execute(
            select(
                func.date(DailyBonusClaim.created_at).label("d"),
                func.count(),
                func.coalesce(func.sum(DailyBonusClaim.amount), 0),
                func.count(func.distinct(DailyBonusClaim.user_id)),
            )
            .where(DailyBonusClaim.created_at >= ctx.since)
            .group_by(func.date(DailyBonusClaim.created_at))
            .order_by(func.date(DailyBonusClaim.created_at))
        )
    ).all()
    return [
        {"date": str(d), "claims": int(c or 0), "amount": round(float(a or 0), 2), "users": int(u or 0)}
        for d, c, a, u in rows
    ]


async def _streaks(ctx: StatsCtx) -> list[dict]:
    """Сколько выдач пришлось на каждый день серии."""
    rows = (
        await ctx.session.execute(
            select(DailyBonusClaim.streak, func.count(), func.coalesce(func.sum(DailyBonusClaim.amount), 0))
            .where(DailyBonusClaim.created_at >= ctx.since)
            .group_by(DailyBonusClaim.streak)
            .order_by(DailyBonusClaim.streak)
        )
    ).all()
    return [{"day": int(s or 0), "claims": int(c or 0), "amount": round(float(a or 0), 2)} for s, c, a in rows]


async def _top_users(ctx: StatsCtx, limit: int = 10) -> list[dict]:
    """Кто забрал больше всех за период."""
    rows = (
        await ctx.session.execute(
            select(
                DailyBonusClaim.user_id,
                func.max(DailyBonusClaim.tg_id),
                func.count(),
                func.coalesce(func.sum(DailyBonusClaim.amount), 0),
                func.max(DailyBonusClaim.streak),
            )
            .where(DailyBonusClaim.created_at >= ctx.since)
            .group_by(DailyBonusClaim.user_id)
            .order_by(func.coalesce(func.sum(DailyBonusClaim.amount), 0).desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "user_id": int(uid or 0),
            "tg_id": int(tg) if tg is not None else None,
            "claims": int(c or 0),
            "amount": round(float(a or 0), 2),
            "best_streak": int(st or 0),
        }
        for uid, tg, c, a, st in rows
    ]


async def _returning_share(ctx: StatsCtx) -> float:
    """Доля забравших бонус больше одного раза за период."""
    per_user = (
        select(DailyBonusClaim.user_id, func.count().label("cnt"))
        .where(DailyBonusClaim.created_at >= ctx.since)
        .group_by(DailyBonusClaim.user_id)
        .subquery()
    )
    total = int(await ctx.scalar(select(func.count()).select_from(per_user)))
    if total == 0:
        return 0.0
    repeat = int(await ctx.scalar(select(func.count()).select_from(per_user).where(per_user.c.cnt > 1)))
    return round(100.0 * repeat / total, 1)


async def bonus(ctx: StatsCtx) -> dict:
    """Ежедневный бонус: выдачи, суммы, серии, кто забирает и текущие правила."""
    rules = resolve_daily_bonus_rules()
    period = DailyBonusClaim.created_at >= ctx.since

    claims_period = int(await ctx.scalar(select(func.count()).select_from(DailyBonusClaim).where(period)))
    amount_period = float(await ctx.scalar(select(func.coalesce(func.sum(DailyBonusClaim.amount), 0)).where(period)))
    users_period = int(await ctx.scalar(select(func.count(func.distinct(DailyBonusClaim.user_id))).where(period)))
    claims_total = int(await ctx.scalar(select(func.count()).select_from(DailyBonusClaim)))
    amount_total = float(await ctx.scalar(select(func.coalesce(func.sum(DailyBonusClaim.amount), 0))))
    users_total = int(await ctx.scalar(select(func.count(func.distinct(DailyBonusClaim.user_id)))))
    best_streak = int(await ctx.scalar(select(func.coalesce(func.max(DailyBonusClaim.streak), 0))))
    live_streaks = int(
        await ctx.scalar(
            select(func.count()).select_from(
                select(DailyBonusClaim.user_id)
                .where(period, DailyBonusClaim.streak > 1)
                .group_by(DailyBonusClaim.user_id)
                .subquery()
            )
        )
    )
    with_sub = int(
        await ctx.scalar(
            select(func.count(func.distinct(DailyBonusClaim.user_id)))
            .select_from(DailyBonusClaim)
            .join(
                Key,
                and_(
                    Key.user_id == DailyBonusClaim.user_id,
                    Key.expiry_time > ctx.now_ms,
                    Key.is_frozen.is_(False),
                ),
            )
            .where(period)
        )
    )
    paid_after = int(
        await ctx.scalar(
            select(func.count(func.distinct(DailyBonusClaim.user_id)))
            .select_from(DailyBonusClaim)
            .join(
                Payment,
                and_(
                    Payment.user_id == DailyBonusClaim.user_id,
                    Payment.status == "success",
                    Payment.payment_system.notin_(INTERNAL_SYSTEMS),
                    Payment.created_at >= DailyBonusClaim.created_at,
                ),
            )
            .where(period)
        )
    )

    return {
        "enabled": rules.enabled,
        "rules": {
            "mode": rules.mode,
            "amount": rules.amount,
            "min_amount": rules.min_amount,
            "max_amount": rules.max_amount,
            "ladder": list(rules.ladder),
            "cooldown_hours": rules.cooldown_hours,
            "period_hours": rules.period_hours,
            "claims_per_period": rules.claims_per_period,
            "streak_keep_hours": rules.streak_keep_hours,
            "require_subscription": rules.require_subscription,
            "min_account_age_hours": rules.min_account_age_hours,
        },
        "totals": {
            "claims_period": claims_period,
            "amount_period": round(amount_period, 2),
            "users_period": users_period,
            "claims_total": claims_total,
            "amount_total": round(amount_total, 2),
            "users_total": users_total,
            "avg_amount": round(amount_period / claims_period, 2) if claims_period else 0.0,
            "avg_claims_per_user": round(claims_period / users_period, 2) if users_period else 0.0,
            "best_streak": best_streak,
            "users_with_streak": live_streaks,
            "returning_pct": await _returning_share(ctx),
            "claimers_with_subscription": with_sub,
            "claimers_paid_after": paid_after,
            "paid_after_pct": round(100.0 * paid_after / users_period, 1) if users_period else 0.0,
        },
        "series": await _series(ctx),
        "streaks": await _streaks(ctx),
        "top": await _top_users(ctx),
    }
