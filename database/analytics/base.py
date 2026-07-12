from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment, User
from database.subscription_events import _INTERNAL_PAYMENT_SYSTEMS as INTERNAL_SYSTEMS

DAY_MS = 86_400_000


class StatsCtx:
    def __init__(self, session: AsyncSession, days: int):
        self.session = session
        self.days = days
        self.now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.since = datetime.utcnow() - timedelta(days=days)
        self.since_ms = int(self.since.timestamp() * 1000)

    async def scalar(self, stmt):
        return (await self.session.execute(stmt)).scalar() or 0


async def revenue_series(ctx: StatsCtx) -> list[dict]:
    """Выручка по дням за период."""
    rows = (
        await ctx.session.execute(
            select(func.date(Payment.created_at).label("d"), func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "success", Payment.created_at >= ctx.since)
            .group_by(func.date(Payment.created_at))
            .order_by(func.date(Payment.created_at))
        )
    ).all()
    return [{"date": str(d), "amount": float(a or 0)} for d, a in rows]


async def revenue_by_system(ctx: StatsCtx) -> list[dict]:
    """Выручка по платёжным системам за период."""
    rows = (
        await ctx.session.execute(
            select(Payment.payment_system, func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "success", Payment.created_at >= ctx.since)
            .group_by(Payment.payment_system)
            .order_by(func.coalesce(func.sum(Payment.amount), 0).desc())
        )
    ).all()
    return [{"name": (name or "—"), "amount": float(a or 0)} for name, a in rows]


async def users_series(ctx: StatsCtx) -> list[dict]:
    """Новые пользователи по дням за период."""
    rows = (
        await ctx.session.execute(
            select(func.date(User.created_at).label("d"), func.count())
            .where(User.created_at >= ctx.since)
            .group_by(func.date(User.created_at))
            .order_by(func.date(User.created_at))
        )
    ).all()
    return [{"date": str(d), "count": int(c or 0)} for d, c in rows]
