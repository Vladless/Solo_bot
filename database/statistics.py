from datetime import date, datetime

from sqlalchemy import and_, func, not_, select, exists
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Payment, Referral, Tariff, User


async def count_total_users(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User))


async def count_users_updated_today(session: AsyncSession, today: date) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.updated_at >= today)
    )


async def count_users_registered_since(session: AsyncSession, since: date) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.created_at >= since)
    )


async def count_users_registered_between(
    session: AsyncSession, start: date, end: date
) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(User)
        .where(User.created_at >= start, User.created_at < end)
    )


async def count_total_keys(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Key))


async def count_active_keys(session: AsyncSession) -> int:
    current_time_ms = int(datetime.utcnow().timestamp() * 1000)
    return await session.scalar(
        select(func.count()).select_from(Key).where(Key.expiry_time > current_time_ms)
    )


async def count_trial_keys(session: AsyncSession) -> int:
    subquery_success_payments = (
        select(Payment.tg_id)
        .where(and_(Payment.tg_id == Key.tg_id, Payment.status == "success"))
        .exists()
    )

    return await session.scalar(
        select(func.count()).select_from(Key).where(not_(subquery_success_payments))
    )


async def get_tariff_distribution(
    session: AsyncSession, include_unbound: bool = False
) -> tuple[list[tuple[int, int]], list[dict]]:
    result = await session.execute(
        select(Key.tariff_id, func.count(Key.client_id))
        .where(Key.tariff_id.isnot(None))
        .group_by(Key.tariff_id)
    )
    tariff_counts = result.all()

    if not include_unbound:
        return tariff_counts

    result = await session.execute(
        select(Key.expiry_time)
        .where(Key.tariff_id.is_(None))
    )
    no_tariff_keys = [{"expiry_time": row[0]} for row in result.all()]

    return tariff_counts, no_tariff_keys


async def get_tariff_names(
    session: AsyncSession, tariff_ids: list[int]
) -> dict[int, str]:
    if not tariff_ids:
        return {}

    result = await session.execute(
        select(Tariff.id, Tariff.name).where(Tariff.id.in_(tariff_ids))
    )
    return dict(result.all())


async def get_tariff_groups(
    session: AsyncSession, tariff_ids: list[int]
) -> dict[int, str]:
    if not tariff_ids:
        return {}

    result = await session.execute(
        select(Tariff.id, Tariff.group_code).where(Tariff.id.in_(tariff_ids))
    )
    return dict(result.all())


async def get_tariff_durations(
    session: AsyncSession, tariff_ids: list[int]
) -> dict[int, int]:
    if not tariff_ids:
        return {}

    result = await session.execute(
        select(Tariff.id, Tariff.duration_days).where(Tariff.id.in_(tariff_ids))
    )
    return dict(result.all())


async def count_total_referrals(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Referral))


async def sum_payments_since(session: AsyncSession, since: date) -> float:
    result = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            and_(
                Payment.created_at >= since,
                Payment.payment_system.notin_(["referral", "coupon"])
            )
        )
    )
    return round(float(result), 2)


async def sum_payments_between(session: AsyncSession, start: date, end: date) -> float:
    result = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            and_(
                Payment.created_at >= start,
                Payment.created_at < end,
                Payment.payment_system.notin_(["referral", "coupon"])
            )
        )
    )
    return round(float(result), 2)


async def sum_total_payments(session: AsyncSession) -> float:
    result = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.payment_system.notin_(["referral", "coupon"])
        )
    )
    return round(float(result), 2)


async def count_hot_leads(session: AsyncSession) -> int:
    subquery_active_keys = (
        select(Key.tg_id)
        .where(Key.expiry_time > int(datetime.utcnow().timestamp() * 1000))
        .distinct()
    )

    stmt = (
        select(Payment.tg_id)
        .where(Payment.status == "success")
        .where(not_(exists(subquery_active_keys.where(Key.tg_id == Payment.tg_id))))
        .distinct()
    )

    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar()