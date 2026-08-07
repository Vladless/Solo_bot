from sqlalchemy import func, select

from database.models import Coupon, Gift, Key, Payment, User, WebErrorReport
from database.users import exclude_shadow_placeholders

from .base import DAY_MS, StatsCtx, revenue_by_system, revenue_series, users_series


async def overview(ctx: StatsCtx) -> dict:
    """Сводка для главной: KPI + ряды одним плоским объектом."""
    now_ms, since, since_ms = ctx.now_ms, ctx.since, ctx.since_ms
    soon_ms = now_ms + 7 * DAY_MS

    total_users = await ctx.scalar(select(func.count()).select_from(User).where(exclude_shadow_placeholders()))
    total_keys = await ctx.scalar(select(func.count()).select_from(Key))
    active_keys = await ctx.scalar(
        select(func.count()).select_from(Key).where(Key.expiry_time > now_ms, Key.is_frozen.is_(False))
    )
    new_users = await ctx.scalar(
        select(func.count()).select_from(User).where(User.created_at >= since, exclude_shadow_placeholders())
    )
    revenue = await ctx.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == "success", Payment.created_at >= since
        )
    )
    revenue_total = await ctx.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "success")
    )
    frozen_keys = await ctx.scalar(select(func.count()).select_from(Key).where(Key.is_frozen.is_(True)))
    trial_users = await ctx.scalar(
        select(func.count()).select_from(User).where(User.trial > 0, exclude_shadow_placeholders())
    )
    new_keys = await ctx.scalar(select(func.count()).select_from(Key).where(Key.created_at >= since_ms))
    expiring_soon = await ctx.scalar(
        select(func.count())
        .select_from(Key)
        .where(Key.expiry_time > now_ms, Key.expiry_time <= soon_ms, Key.is_frozen.is_(False))
    )
    payments_count = await ctx.scalar(
        select(func.count()).select_from(Payment).where(Payment.status == "success", Payment.created_at >= since)
    )
    gifts_total = await ctx.scalar(select(func.count()).select_from(Gift))
    coupons_total = await ctx.scalar(select(func.count()).select_from(Coupon))
    try:
        errors_open = await ctx.scalar(
            select(func.count()).select_from(WebErrorReport).where(WebErrorReport.resolved.is_(False))
        )
    except Exception:
        errors_open = 0
    avg_check = float(revenue) / int(payments_count) if payments_count else 0.0

    return {
        "period_days": ctx.days,
        "total_users": int(total_users),
        "new_users": int(new_users),
        "total_keys": int(total_keys),
        "active_keys": int(active_keys),
        "frozen_keys": int(frozen_keys),
        "trial_users": int(trial_users),
        "new_keys": int(new_keys),
        "expiring_soon": int(expiring_soon),
        "payments_count": int(payments_count),
        "gifts_total": int(gifts_total),
        "coupons_total": int(coupons_total),
        "errors_open": int(errors_open),
        "avg_check": float(avg_check),
        "revenue_period": float(revenue),
        "revenue_total": float(revenue_total),
        "revenue_series": await revenue_series(ctx),
        "revenue_by_system": await revenue_by_system(ctx),
        "users_series": await users_series(ctx),
    }
