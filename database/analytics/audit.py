from datetime import datetime

from .base import StatsCtx


async def audit(ctx: StatsCtx) -> dict:
    """Аудит: статистика путей + воронка старт→оплата за период."""
    from audit import get_audit_funnel, get_audit_stats

    date_to = datetime.utcnow()
    stats = await get_audit_stats(ctx.session, date_from=ctx.since, date_to=date_to)
    funnel = await get_audit_funnel(ctx.session, date_from=ctx.since, date_to=date_to)
    return {**stats, "funnel": funnel}
