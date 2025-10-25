from datetime import date, datetime

from sqlalchemy import and_, exists, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Payment, Referral, Tariff, User


async def count_total_users(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User))


async def count_users_updated_today(session: AsyncSession, today: date) -> int:
    return await session.scalar(select(func.count()).select_from(User).where(User.updated_at >= today))


async def count_users_registered_since(session: AsyncSession, since: date) -> int:
    return await session.scalar(select(func.count()).select_from(User).where(User.created_at >= since))


async def count_users_registered_between(session: AsyncSession, start: date, end: date) -> int:
    return await session.scalar(
        select(func.count()).select_from(User).where(User.created_at >= start, User.created_at < end)
    )


async def count_total_keys(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Key))


async def count_active_keys(session: AsyncSession) -> int:
    current_time_ms = int(datetime.utcnow().timestamp() * 1000)
    return await session.scalar(select(func.count()).select_from(Key).where(Key.expiry_time > current_time_ms))


async def count_trial_keys(session: AsyncSession) -> int:
    trial_tariffs_subquery = select(Tariff.id).where(Tariff.group_code == "trial")

    return await session.scalar(select(func.count()).select_from(Key).where(Key.tariff_id.in_(trial_tariffs_subquery)))


async def get_tariff_distribution(
    session: AsyncSession, include_unbound: bool = False
) -> tuple[list[tuple[int, int]], list[dict]]:
    result = await session.execute(
        select(Key.tariff_id, func.count(Key.client_id)).where(Key.tariff_id.isnot(None)).group_by(Key.tariff_id)
    )
    tariff_counts = result.all()

    if not include_unbound:
        return tariff_counts

    result = await session.execute(select(Key.expiry_time).where(Key.tariff_id.is_(None)))
    no_tariff_keys = [{"expiry_time": row[0]} for row in result.all()]

    return tariff_counts, no_tariff_keys


async def get_tariff_names(session: AsyncSession, tariff_ids: list[int]) -> dict[int, str]:
    if not tariff_ids:
        return {}

    result = await session.execute(select(Tariff.id, Tariff.name).where(Tariff.id.in_(tariff_ids)))
    return dict(result.all())


async def get_tariff_groups(session: AsyncSession, tariff_ids: list[int]) -> dict[int, str]:
    if not tariff_ids:
        return {}

    result = await session.execute(select(Tariff.id, Tariff.group_code).where(Tariff.id.in_(tariff_ids)))
    return dict(result.all())


async def get_tariff_durations(session: AsyncSession, tariff_ids: list[int]) -> dict[int, int]:
    if not tariff_ids:
        return {}

    result = await session.execute(select(Tariff.id, Tariff.duration_days).where(Tariff.id.in_(tariff_ids)))
    return dict(result.all())


async def count_total_referrals(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Referral))


async def sum_payments_since(session: AsyncSession, since: date) -> float:
    result = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            and_(
                Payment.created_at >= since,
                Payment.status == "success",
                Payment.payment_system.notin_(["referral", "coupon", "cashback"]),
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
                Payment.status == "success",
                Payment.payment_system.notin_(["referral", "coupon", "cashback"]),
            )
        )
    )
    return round(float(result), 2)


async def sum_total_payments(session: AsyncSession) -> float:
    result = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            and_(
                Payment.status == "success",
                Payment.payment_system.notin_(["referral", "coupon", "cashback"]),
            )
        )
    )
    return round(float(result), 2)


async def count_hot_leads(session: AsyncSession) -> int:
    subquery_active_keys = (
        select(Key.tg_id).where(Key.expiry_time > int(datetime.utcnow().timestamp() * 1000)).distinct()
    )

    stmt = (
        select(Payment.tg_id)
        .where(Payment.amount > 0)
        .where(Payment.status == "success")
        .where(Payment.payment_system.notin_(["referral", "coupon", "cashback"]))
        .where(not_(exists(subquery_active_keys.where(Key.tg_id == Payment.tg_id))))
        .distinct()
    )

    result = await session.execute(select(func.count()).select_from(stmt.subquery()))
    return result.scalar()
