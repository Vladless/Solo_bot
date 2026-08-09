from __future__ import annotations

import inspect

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    DailySubscriptionMetric,
    Key,
    Payment,
    SubscriptionEvent,
    User,
)
from database.users import exclude_shadow_placeholders
from logger import logger


_INTERNAL_PAYMENT_SYSTEMS = ("referral", "cashback", "coupon", "admin")


_DEDUP_EVENT_TYPES = ("renewed", "created")
_DEDUP_WINDOW_SEC = 120


async def _already_recorded(session: AsyncSession, event_type: str, client_id, expiry_time) -> bool:
    """Событие того же типа с тем же ключом и сроком уже записано."""
    if event_type not in _DEDUP_EVENT_TYPES or not client_id or expiry_time is None:
        return False
    try:
        since = datetime.utcnow() - timedelta(seconds=_DEDUP_WINDOW_SEC)
        for pending in session.new:
            if (
                isinstance(pending, SubscriptionEvent)
                and pending.event_type == event_type
                and pending.client_id == str(client_id)[:128]
                and pending.expiry_time == expiry_time
            ):
                return True
        found = await session.scalar(
            select(SubscriptionEvent.id)
            .where(
                SubscriptionEvent.event_type == event_type,
                SubscriptionEvent.client_id == str(client_id)[:128],
                SubscriptionEvent.expiry_time == expiry_time,
                SubscriptionEvent.created_at >= since,
            )
            .limit(1)
        )
        return found is not None
    except Exception:
        return False


async def record_subscription_event(
    session: AsyncSession,
    *,
    event_type: str,
    user_id: int | None = None,
    tg_id: int | None = None,
    client_id: str | None = None,
    tariff_id: int | None = None,
    server_id: str | None = None,
    price_rub: float | None = None,
    duration_days: int | None = None,
    expiry_time: int | None = None,
    was_expired: bool | None = None,
    source: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Добавляет событие в журнал. Никогда не бросает исключение — логирование
    подписок не должно ломать операции с ключами. Коммит — за вызывающим (контракт транзакций)."""
    try:
        if await _already_recorded(session, event_type, client_id, expiry_time):
            return
        add_result = session.add(
            SubscriptionEvent(
                event_type=event_type,
                user_id=user_id,
                tg_id=tg_id,
                client_id=str(client_id)[:128] if client_id else None,
                tariff_id=tariff_id,
                server_id=server_id,
                price_rub=float(price_rub) if price_rub is not None else None,
                duration_days=duration_days,
                expiry_time=expiry_time,
                was_expired=was_expired,
                source=source,
                metadata_=metadata,
            )
        )
        if inspect.isawaitable(add_result):
            await add_result
    except Exception as e:
        logger.warning("[sub-events] не удалось записать событие {}: {}", event_type, e)


async def resolve_user_ref_by_client_id(session: AsyncSession, client_id: str) -> tuple[int | None, str | None]:
    """По client_id подписки находит ссылку на пользователя (tg_id или user_id — обе годятся
    для resolve_user_optional). Сначала активный ключ, затем журнал событий.
    Возвращает (ref, source), source ∈ {"active", "history", None}."""
    needle = (client_id or "").strip().lower()
    if not needle:
        return None, None

    row = (
        await session.execute(select(Key.tg_id, Key.user_id).where(func.lower(Key.client_id) == needle).limit(1))
    ).first()
    if row and (row.tg_id is not None or row.user_id is not None):
        return (row.tg_id if row.tg_id is not None else row.user_id), "active"

    ev = (
        await session.execute(
            select(SubscriptionEvent.tg_id, SubscriptionEvent.user_id)
            .where(func.lower(SubscriptionEvent.client_id) == needle)
            .order_by(SubscriptionEvent.created_at.desc())
            .limit(1)
        )
    ).first()
    if ev and (ev.tg_id is not None or ev.user_id is not None):
        return (ev.tg_id if ev.tg_id is not None else ev.user_id), "history"

    return None, None


async def get_user_subscription_history(
    session: AsyncSession, *, user_id: int | None = None, tg_id: int | None = None
) -> list[dict]:
    """Группирует журнал событий по client_id в «жизни» подписок пользователя:
    когда появилась, сколько продлений, до какого срока, чем закончилась.
    Самые свежие — первыми."""
    refs = []
    if user_id is not None:
        refs.append(SubscriptionEvent.user_id == user_id)
    if tg_id is not None:
        refs.append(SubscriptionEvent.tg_id == tg_id)
    if not refs:
        return []

    rows = (
        await session.execute(
            select(
                SubscriptionEvent.client_id,
                SubscriptionEvent.event_type,
                SubscriptionEvent.tariff_id,
                SubscriptionEvent.server_id,
                SubscriptionEvent.expiry_time,
                SubscriptionEvent.created_at,
            )
            .where(or_(*refs))
            .where(SubscriptionEvent.client_id.isnot(None))
            .order_by(SubscriptionEvent.created_at)
        )
    ).all()

    groups: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        cid = r.client_id
        g = groups.get(cid)
        if g is None:
            g = {
                "client_id": cid,
                "first_at": r.created_at,
                "last_at": r.created_at,
                "renewals": 0,
                "tariff_id": r.tariff_id,
                "server_id": r.server_id,
                "max_expiry": r.expiry_time,
                "last_event": r.event_type,
            }
            groups[cid] = g
            order.append(cid)
        else:
            g["last_at"] = r.created_at
            if r.tariff_id is not None:
                g["tariff_id"] = r.tariff_id
            if r.server_id is not None:
                g["server_id"] = r.server_id
            g["last_event"] = r.event_type
        if r.expiry_time and (g["max_expiry"] is None or r.expiry_time > g["max_expiry"]):
            g["max_expiry"] = r.expiry_time
        if r.event_type == "renewed":
            g["renewals"] += 1

    return [groups[c] for c in reversed(order)]


async def get_recent_renewals(session: AsyncSession, client_id: str, limit: int = 5) -> list[dict]:
    """Последние операции продления (event_type=renewed) по client_id, свежие первыми."""
    if not client_id:
        return []
    rows = (
        await session.execute(
            select(
                SubscriptionEvent.created_at,
                SubscriptionEvent.tariff_id,
                SubscriptionEvent.expiry_time,
                SubscriptionEvent.source,
                SubscriptionEvent.duration_days,
                SubscriptionEvent.price_rub,
            )
            .where(SubscriptionEvent.client_id == client_id)
            .where(SubscriptionEvent.event_type == "renewed")
            .order_by(SubscriptionEvent.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "created_at": r.created_at,
            "tariff_id": r.tariff_id,
            "expiry_time": r.expiry_time,
            "source": r.source,
            "duration_days": r.duration_days,
            "price_rub": r.price_rub,
        }
        for r in rows
    ]


async def backfill_from_payments(session: AsyncSession) -> int:
    """Ретроспективно засеивает журнал из истории платежей: первый успешный платёж
    юзера → created, последующие → renewed. Идемпотентно (пропускает, если уже сеяли)."""
    already = await session.scalar(
        select(func.count()).select_from(SubscriptionEvent).where(SubscriptionEvent.source == "backfill")
    )
    if already:
        return 0

    rows = (
        await session.execute(
            select(Payment.user_id, Payment.tg_id, Payment.amount, Payment.created_at)
            .where(Payment.status == "success")
            .where(Payment.payment_system.notin_(_INTERNAL_PAYMENT_SYSTEMS))
            .order_by(Payment.user_id, Payment.created_at)
        )
    ).all()

    seen: set[int] = set()
    count = 0
    for r in rows:
        uid = r.user_id
        is_first = uid not in seen
        if uid is not None:
            seen.add(uid)
        ev = SubscriptionEvent(
            event_type="created" if is_first else "renewed",
            user_id=uid,
            tg_id=r.tg_id,
            price_rub=float(r.amount) if r.amount is not None else None,
            source="backfill",
            created_at=r.created_at,
        )
        session.add(ev)
        count += 1
    logger.info("[sub-events] backfill из платежей: засеяно {} событий", count)
    return count


async def snapshot_daily_metrics(session: AsyncSession) -> None:
    """Дневной снапшот: активные подписки сейчас + события за прошедшие сутки (UTC).
    Пишет/обновляет строку за вчерашний день."""
    now = datetime.utcnow()
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    target_date = (now - timedelta(days=1)).date()
    day_start = datetime(target_date.year, target_date.month, target_date.day)
    day_end = day_start + timedelta(days=1)

    active = (await session.scalar(select(func.count()).select_from(Key).where(Key.expiry_time > now_ms))) or 0

    ev_rows = (
        await session.execute(
            select(SubscriptionEvent.event_type, func.count().label("cnt"))
            .where(SubscriptionEvent.created_at >= day_start)
            .where(SubscriptionEvent.created_at < day_end)
            .group_by(SubscriptionEvent.event_type)
        )
    ).all()
    ev = {r.event_type: int(r.cnt) for r in ev_rows}

    revenue = (
        await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "success")
            .where(Payment.payment_system.notin_(_INTERNAL_PAYMENT_SYSTEMS))
            .where(Payment.created_at >= day_start)
            .where(Payment.created_at < day_end)
        )
    ) or 0

    tariff_rows = (
        await session.execute(
            select(Key.tariff_id, func.count().label("cnt")).where(Key.expiry_time > now_ms).group_by(Key.tariff_id)
        )
    ).all()
    server_rows = (
        await session.execute(
            select(Key.server_id, func.count().label("cnt")).where(Key.expiry_time > now_ms).group_by(Key.server_id)
        )
    ).all()
    by_tariff = {str(r.tariff_id): int(r.cnt) for r in tariff_rows}
    by_server = {str(r.server_id): int(r.cnt) for r in server_rows}

    values = {
        "snapshot_date": target_date,
        "active": int(active),
        "created": ev.get("created", 0),
        "renewed": ev.get("renewed", 0),
        "expired": ev.get("expired", 0),
        "deleted": ev.get("deleted", 0),
        "revenue_rub": float(revenue),
        "by_tariff": by_tariff,
        "by_server": by_server,
    }
    stmt = pg_insert(DailySubscriptionMetric).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DailySubscriptionMetric.snapshot_date],
        set_={k: v for k, v in values.items() if k != "snapshot_date"},
    )
    await session.execute(stmt)


async def get_subscription_dynamics(session: AsyncSession, days: int) -> dict:
    """Данные для аналитики: события подписок по дням (из журнала, есть backfill-история)
    + тренд активных подписок по дням (из снапшотов, накапливается с момента деплоя)."""
    since = datetime.utcnow() - timedelta(days=days)

    day = func.date_trunc("day", SubscriptionEvent.created_at).label("day")
    ev_rows = (
        await session.execute(
            select(day, SubscriptionEvent.event_type, func.count().label("cnt"))
            .where(SubscriptionEvent.created_at >= since)
            .group_by(day, SubscriptionEvent.event_type)
            .order_by(day)
        )
    ).all()
    daily_map: dict[str, dict[str, int]] = {}
    for r in ev_rows:
        d = r.day.strftime("%Y-%m-%d")
        cur = daily_map.setdefault(d, {"created": 0, "renewed": 0, "expired": 0, "deleted": 0})
        if r.event_type in cur:
            cur[r.event_type] = int(r.cnt)

    since_date = since.date()
    snap_rows = (
        await session.execute(
            select(DailySubscriptionMetric.snapshot_date, DailySubscriptionMetric.active)
            .where(DailySubscriptionMetric.snapshot_date >= since_date)
            .order_by(DailySubscriptionMetric.snapshot_date)
        )
    ).all()

    return {
        "dailyEvents": [
            {
                "date": d,
                "created": v["created"],
                "renewed": v["renewed"],
                "expired": v["expired"] + v["deleted"],
            }
            for d, v in sorted(daily_map.items())
        ],
        "activeTrend": [{"date": r.snapshot_date.strftime("%Y-%m-%d"), "active": int(r.active)} for r in snap_rows],
    }


async def get_retention_metrics(session: AsyncSession, days: int) -> dict:
    """Удержание: churn rate, LTV, trial→paid конверсия и когортная удержанность по месяцам."""
    from collections import defaultdict

    now = datetime.utcnow()
    since = now - timedelta(days=days)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    expired = (
        await session.scalar(
            select(func.count())
            .select_from(SubscriptionEvent)
            .where(SubscriptionEvent.event_type.in_(["expired", "deleted"]))
            .where(SubscriptionEvent.created_at >= since)
        )
    ) or 0
    active = (await session.scalar(select(func.count()).select_from(Key).where(Key.expiry_time > now_ms))) or 0
    churn_rate = (expired / (active + expired) * 100.0) if (active + expired) else 0.0

    total_rev = (
        await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .where(Payment.status == "success")
            .where(Payment.payment_system.notin_(_INTERNAL_PAYMENT_SYSTEMS))
        )
    ) or 0
    payers = (
        await session.scalar(
            select(func.count(func.distinct(Payment.user_id)))
            .where(Payment.status == "success")
            .where(Payment.payment_system.notin_(_INTERNAL_PAYMENT_SYSTEMS))
        )
    ) or 0
    ltv = (float(total_rev) / payers) if payers else 0.0

    trial_users = (
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.created_at >= since)
            .where(User.trial > 0)
            .where(exclude_shadow_placeholders())
        )
    ) or 0
    paid_uids = (
        select(Payment.user_id)
        .where(Payment.status == "success")
        .where(Payment.payment_system.notin_(_INTERNAL_PAYMENT_SYSTEMS))
    )
    trial_converted = (
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.created_at >= since)
            .where(User.trial > 0)
            .where(User.id.in_(paid_uids))
        )
    ) or 0
    trial_rate = (trial_converted / trial_users * 100.0) if trial_users else 0.0

    cohort_rows = (
        await session.execute(
            select(SubscriptionEvent.user_id, SubscriptionEvent.created_at)
            .where(SubscriptionEvent.event_type.in_(["created", "renewed"]))
            .where(SubscriptionEvent.created_at >= now - timedelta(days=210))
            .where(SubscriptionEvent.user_id.isnot(None))
        )
    ).all()
    user_months: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for r in cohort_rows:
        user_months[r.user_id].add((r.created_at.year, r.created_at.month))

    def mi(m: tuple[int, int]) -> int:
        return m[0] * 12 + (m[1] - 1)

    cohorts: dict[tuple[int, int], dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for uid, months in user_months.items():
        first = min(months, key=mi)
        fi = mi(first)
        for m in months:
            cohorts[first][mi(m) - fi].add(uid)

    max_off = 6
    out_cohorts = []
    for c in sorted(cohorts.keys(), key=mi)[-6:]:
        out_cohorts.append({
            "month": f"{c[0]}-{c[1]:02d}",
            "size": len(cohorts[c].get(0, set())),
            "retention": [len(cohorts[c].get(off, set())) for off in range(max_off + 1)],
        })

    return {
        "churnRate": round(churn_rate, 1),
        "ltvRub": round(ltv, 2),
        "trialUsers": int(trial_users),
        "trialConverted": int(trial_converted),
        "trialRate": round(trial_rate, 1),
        "cohorts": out_cohorts,
    }
