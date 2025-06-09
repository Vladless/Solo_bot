from sqlalchemy import func, insert, select, not_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Payment, TrackingSource, User
from logger import logger


async def create_tracking_source(
    session: AsyncSession, name: str, code: str, type_: str, created_by: int
):
    try:
        stmt = insert(TrackingSource).values(
            name=name,
            code=code,
            type=type_,
            created_by=created_by,
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"🆕 Источник трафика {code} создан")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при создании источника {code}: {e}")
        await session.rollback()


async def get_all_tracking_sources(session: AsyncSession) -> list[dict]:
    registrations_subq = (
        select(func.count(func.distinct(User.tg_id)))
        .where(User.source_code == TrackingSource.code)
        .correlate(TrackingSource)
        .scalar_subquery()
    )

    trials_subq = (
        select(func.count(func.distinct(User.tg_id)))
        .where((User.source_code == TrackingSource.code) & (User.trial == 1))
        .correlate(TrackingSource)
        .scalar_subquery()
    )

    payments_subq = (
        select(func.count(func.distinct(Payment.tg_id)))
        .join(User, Payment.tg_id == User.tg_id)
        .where(
            (User.source_code == TrackingSource.code) & (Payment.status == "success")
        )
        .correlate(TrackingSource)
        .scalar_subquery()
    )

    query = select(
        TrackingSource.code,
        TrackingSource.name,
        TrackingSource.created_at,
        registrations_subq.label("registrations"),
        trials_subq.label("trials"),
        payments_subq.label("payments"),
    ).order_by(TrackingSource.created_at.desc())

    result = await session.execute(query)
    rows = result.all()
    return [
        {
            "code": r.code,
            "name": r.name,
            "created_at": r.created_at,
            "registrations": r.registrations or 0,
            "trials": r.trials or 0,
            "payments": r.payments or 0,
        }
        for r in rows
    ]


async def get_tracking_source_stats(session: AsyncSession, code: str) -> dict | None:
    reg_subq = (
        select(func.count(func.distinct(User.tg_id)))
        .where(User.source_code == code)
        .scalar_subquery()
    )

    trial_subq = (
        select(func.count(func.distinct(User.tg_id)))
        .where((User.source_code == code) & (User.trial == 1))
        .scalar_subquery()
    )

    payments_subq = (
        select(func.count(func.distinct(Payment.tg_id)))
        .join(User, Payment.tg_id == User.tg_id)
        .where(
            (User.source_code == code)
            & (Payment.status == "success")
            & not_(Payment.payment_system.in_(["coupon", "referral"]))
        )
        .scalar_subquery()
    )

    amount_subq = (
        select(func.coalesce(func.sum(Payment.amount), 0))
        .join(User, Payment.tg_id == User.tg_id)
        .where(
            (User.source_code == code)
            & (Payment.status == "success")
            & not_(Payment.payment_system.in_(["coupon", "referral"]))
        )
        .scalar_subquery()
    )

    query = select(
        TrackingSource.name,
        TrackingSource.code,
        TrackingSource.created_at,
        reg_subq.label("registrations"),
        trial_subq.label("trials"),
        payments_subq.label("payments"),
        amount_subq.label("total_amount"),
    ).where(TrackingSource.code == code)

    result = await session.execute(query)
    row = result.first()
    if not row:
        return None

    return {
        "name": row.name,
        "code": row.code,
        "created_at": row.created_at,
        "registrations": row.registrations or 0,
        "trials": row.trials or 0,
        "payments": row.payments or 0,
        "total_amount": float(row.total_amount or 0),
    }
