from sqlalchemy import and_, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.client_origin import normalize_campaign
from core.constants import PAYMENT_SYSTEMS_EXCLUDED
from core.redis_cache import cache_get, cache_key, cache_set
from database.models import Payment, TrackingSource, User
from database.users import upsert_source_if_empty
from logger import logger
from settings.cache_config import START_UTM_EXISTS_TTL_SEC as UTM_EXISTS_TTL_SEC


async def is_known_tracking_source(session: AsyncSession, code: str) -> bool:
    """Есть ли такой код источника. Ответ кешируется: проверка идёт на каждом входе клиента."""
    if not code:
        return False
    key = cache_key("utm_exists", code)
    cached = await cache_get(key)
    if cached is not None:
        return bool(cached)
    exists = (
        await session.execute(select(1).select_from(TrackingSource).where(TrackingSource.code == code).limit(1))
    ).scalar_one_or_none() is not None
    await cache_set(key, bool(exists), UTM_EXISTS_TTL_SEC)
    return exists


async def attribute_source_if_known(session: AsyncSession, tg_id: int, code: str | None) -> bool:
    """Ставит источник клиенту, если код известен и источник ещё не заполнен."""
    normalized = normalize_campaign(code)
    if not normalized or not await is_known_tracking_source(session, normalized):
        return False
    return await upsert_source_if_empty(session, tg_id, normalized)


async def create_tracking_source(session: AsyncSession, name: str, code: str, type_: str, created_by: int):
    stmt = insert(TrackingSource).values(
        name=name,
        code=code,
        type=type_,
        created_by=created_by,
    )
    await session.execute(stmt)
    logger.info(f"🆕 Источник трафика {code} создан")


async def get_all_tracking_sources(session: AsyncSession) -> list[dict]:
    registrations_subq = (
        select(func.count(func.distinct(User.id)))
        .where(User.source_code == TrackingSource.code)
        .correlate(TrackingSource)
        .scalar_subquery()
    )

    trials_subq = (
        select(func.count(func.distinct(User.id)))
        .where((User.source_code == TrackingSource.code) & (User.trial == 1))
        .correlate(TrackingSource)
        .scalar_subquery()
    )

    payments_subq = (
        select(func.count(func.distinct(Payment.user_id)))
        .join(User, Payment.user_id == User.id)
        .where(
            (User.source_code == TrackingSource.code)
            & (Payment.status == "success")
            & Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED)
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
    def _month_key(dt) -> str:
        return dt.strftime("%Y-%m")

    src_row = await session.execute(
        select(TrackingSource.name, TrackingSource.code, TrackingSource.created_at).where(TrackingSource.code == code)
    )
    src = src_row.first()
    if not src:
        return None

    _src_name, _src_code, created_at = src

    reg_subq = (
        select(func.count(func.distinct(User.id)))
        .where((User.source_code == code) & (User.created_at >= created_at))
        .scalar_subquery()
    )

    trial_subq = (
        select(func.count(func.distinct(User.id)))
        .where((User.source_code == code) & (User.trial == 1) & (User.created_at >= created_at))
        .scalar_subquery()
    )

    payments_subq = (
        select(func.count(func.distinct(Payment.user_id)))
        .join(User, Payment.user_id == User.id)
        .where(
            (User.source_code == code)
            & (Payment.status == "success")
            & Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED)
            & (Payment.created_at >= created_at)
        )
        .scalar_subquery()
    )

    amount_subq = (
        select(func.coalesce(func.sum(Payment.amount), 0.0))
        .join(User, Payment.user_id == User.id)
        .where(
            (User.source_code == code)
            & (Payment.status == "success")
            & Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED)
            & (Payment.created_at >= created_at)
        )
        .scalar_subquery()
    )

    header_q = select(
        TrackingSource.name,
        TrackingSource.code,
        TrackingSource.created_at,
        reg_subq.label("registrations"),
        trial_subq.label("trials"),
        payments_subq.label("payments"),
        amount_subq.label("total_amount"),
    ).where(TrackingSource.code == code)

    header_res = await session.execute(header_q)
    row = header_res.first()
    if not row:
        return None

    payments_base = (
        select(
            Payment.user_id.label("tg_id"),
            Payment.amount.label("amount"),
            Payment.created_at.label("dt"),
        )
        .join(User, Payment.user_id == User.id)
        .where(
            (User.source_code == code)
            & (Payment.status == "success")
            & Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED)
            & (Payment.created_at >= created_at)
        )
        .subquery()
    )

    first_pay = (
        select(
            payments_base.c.tg_id.label("tg_id"),
            func.min(payments_base.c.dt).label("first_dt"),
        )
        .group_by(payments_base.c.tg_id)
        .subquery()
    )

    month_expr_new = func.date_trunc("month", payments_base.c.dt).label("month")
    new_rows = await session.execute(
        select(
            month_expr_new,
            func.count().label("cnt"),
            func.coalesce(func.sum(payments_base.c.amount), 0.0).label("amt"),
        )
        .join(
            first_pay,
            and_(
                payments_base.c.tg_id == first_pay.c.tg_id,
                payments_base.c.dt == first_pay.c.first_dt,
            ),
        )
        .group_by(month_expr_new)
        .order_by(month_expr_new)
    )
    new_by_month = {r.month: (int(r.cnt), float(r.amt)) for r in new_rows.all()}

    month_expr_rep = func.date_trunc("month", payments_base.c.dt).label("month")
    repeat_rows = await session.execute(
        select(
            month_expr_rep,
            func.count().label("cnt"),
            func.coalesce(func.sum(payments_base.c.amount), 0.0).label("amt"),
        )
        .join(first_pay, payments_base.c.tg_id == first_pay.c.tg_id)
        .where(payments_base.c.dt > first_pay.c.first_dt)
        .group_by(month_expr_rep)
        .order_by(month_expr_rep)
    )
    repeat_by_month = {r.month: (int(r.cnt), float(r.amt)) for r in repeat_rows.all()}

    month_expr_regs = func.date_trunc("month", User.created_at).label("month")
    regs_rows = await session.execute(
        select(
            month_expr_regs,
            func.count(func.distinct(User.tg_id)).label("cnt"),
        )
        .where((User.source_code == code) & (User.created_at >= created_at))
        .group_by(month_expr_regs)
        .order_by(month_expr_regs)
    )
    regs_by_month = {r.month: int(r.cnt) for r in regs_rows.all()}

    month_expr_trials = func.date_trunc("month", User.created_at).label("month")
    trials_rows = await session.execute(
        select(
            month_expr_trials,
            func.count(func.distinct(User.tg_id)).label("cnt"),
        )
        .where((User.source_code == code) & (User.trial == 1) & (User.created_at >= created_at))
        .group_by(month_expr_trials)
        .order_by(month_expr_trials)
    )
    trials_by_month = {r.month: int(r.cnt) for r in trials_rows.all()}

    months = set()
    months.update(regs_by_month.keys())
    months.update(trials_by_month.keys())
    months.update(new_by_month.keys())
    months.update(repeat_by_month.keys())

    monthly = []
    for m in sorted(months):
        regs = regs_by_month.get(m, 0)
        trls = trials_by_month.get(m, 0)
        new_cnt, new_amt = new_by_month.get(m, (0, 0.0))
        rep_cnt, rep_amt = repeat_by_month.get(m, (0, 0.0))
        monthly.append({
            "month": _month_key(m),
            "registrations": regs,
            "trials": trls,
            "new_purchases_count": new_cnt,
            "new_purchases_amount": new_amt,
            "repeat_purchases_count": rep_cnt,
            "repeat_purchases_amount": rep_amt,
        })

    return {
        "name": row.name,
        "code": row.code,
        "created_at": row.created_at,
        "registrations": row.registrations or 0,
        "trials": row.trials or 0,
        "payments": row.payments or 0,
        "total_amount": float(row.total_amount or 0),
        "monthly": monthly,
    }
