from sqlalchemy import case, func, select

from database.models import Key, Payment, Tariff, User

from .base import INTERNAL_SYSTEMS, StatsCtx, revenue_by_system, revenue_series


async def _mrr(ctx: StatsCtx) -> float:
    """Нормализованный месячный run-rate по активным подпискам."""
    val = await ctx.scalar(
        select(func.coalesce(func.sum(Tariff.price_rub * 30.0 / func.nullif(Tariff.duration_days, 0)), 0))
        .select_from(Key)
        .join(Tariff, Key.tariff_id == Tariff.id)
        .where(Key.expiry_time > ctx.now_ms, Key.is_frozen.is_(False), Tariff.price_rub > 0)
    )
    return round(float(val), 2)


async def _payment_success_rate(ctx: StatsCtx) -> list[dict]:
    """Доля успешных платежей по системам за период."""
    success = func.sum(case((Payment.status == "success", 1), else_=0))
    rows = (
        await ctx.session.execute(
            select(Payment.payment_system, func.count(), success)
            .where(Payment.created_at >= ctx.since)
            .group_by(Payment.payment_system)
            .order_by(func.count().desc())
        )
    ).all()
    out = []
    for name, total, ok in rows:
        total, ok = int(total or 0), int(ok or 0)
        out.append({"system": (name or "—"), "total": total, "success": ok, "rate_pct": round(100.0 * ok / total, 1) if total else 0.0})
    return out


async def revenue(ctx: StatsCtx) -> dict:
    """Деньги: выручка, ряды, MRR, ARPU/ARPPU, успех платежей."""
    real = Payment.payment_system.notin_(INTERNAL_SYSTEMS)
    revenue_period = float(
        await ctx.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "success", Payment.created_at >= ctx.since
            )
        )
    )
    revenue_total = float(
        await ctx.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "success"))
    )
    revenue_real = float(
        await ctx.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "success", Payment.created_at >= ctx.since, real
            )
        )
    )
    payers = int(
        await ctx.scalar(
            select(func.count(func.distinct(Payment.user_id))).where(
                Payment.status == "success", Payment.created_at >= ctx.since, real
            )
        )
    )
    payments_count = int(
        await ctx.scalar(
            select(func.count())
            .select_from(Payment)
            .where(Payment.status == "success", Payment.created_at >= ctx.since, real)
        )
    )
    total_users = int(await ctx.scalar(select(func.count()).select_from(User)))
    return {
        "revenue_period": revenue_period,
        "revenue_total": revenue_total,
        "revenue_real": revenue_real,
        "series": await revenue_series(ctx),
        "by_system": await revenue_by_system(ctx),
        "mrr_rub": await _mrr(ctx),
        "success_rate": await _payment_success_rate(ctx),
        "payers": payers,
        "arpu": round(revenue_real / total_users, 2) if total_users else 0.0,
        "arppu": round(revenue_real / payers, 2) if payers else 0.0,
        "avg_check": round(revenue_real / payments_count, 2) if payments_count else 0.0,
    }
