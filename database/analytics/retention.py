from database.subscription_events import get_retention_metrics, get_subscription_dynamics

from .base import StatsCtx


async def retention(ctx: StatsCtx) -> dict:
    """Удержание: churn / LTV / конверсия триала / когорты + динамика подписок."""
    data = await get_retention_metrics(ctx.session, ctx.days)
    data["dynamics"] = await get_subscription_dynamics(ctx.session, ctx.days)
    return data
