import asyncio
import datetime as _dt
import time

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, KeyTrafficHistory, KeyTrafficHourly
from logger import logger


_GB = 1073741824
_BULK_TIMEOUT_SEC = 60


async def snapshot_all_key_traffic(session: AsyncSession) -> int:
    """Снимает дневной снапшот использованного трафика по активным ключам (remnawave bulk).
    Один ряд на (client_id, дата); повторный запуск за день обновляет значение."""
    from panels.remnawave_runtime import fetch_all_remnawave_traffic

    now_ms = int(time.time() * 1000)
    rows = (
        await session.execute(
            select(Key.client_id).where(
                Key.expiry_time > now_ms,
                Key.is_frozen.isnot(True),
                Key.client_id.isnot(None),
            )
        )
    ).all()
    active = [str(cid) for (cid,) in rows if cid]
    if not active:
        return 0

    needed = set(active)
    try:
        used_map = await asyncio.wait_for(fetch_all_remnawave_traffic(session, needed), timeout=_BULK_TIMEOUT_SEC)
    except (TimeoutError, Exception) as exc:
        logger.warning("[TrafficHistory] не удалось получить bulk-трафик: {}", exc)
        used_map = {}
    if not used_map:
        return 0

    today = _dt.datetime.utcnow().date()
    count = 0
    for cid in active:
        used_bytes = used_map.get(cid)
        if used_bytes is None:
            continue
        used_gb = round(int(used_bytes) / _GB, 3)
        stmt = (
            pg_insert(KeyTrafficHistory)
            .values(client_id=cid, used_gb=used_gb, limit_gb=None, snapshot_date=today)
            .on_conflict_do_update(
                constraint="uq_key_traffic_history_client_date",
                set_={"used_gb": used_gb},
            )
        )
        await session.execute(stmt)
        count += 1
    return count


async def get_traffic_history(session: AsyncSession, client_id: str, days: int = 30) -> list[dict]:
    days = max(1, min(365, days))
    today = _dt.datetime.utcnow().date()
    since = today - _dt.timedelta(days=days - 1)
    baseline = (
        await session.execute(
            select(KeyTrafficHistory.used_gb)
            .where(KeyTrafficHistory.client_id == client_id, KeyTrafficHistory.snapshot_date < since)
            .order_by(KeyTrafficHistory.snapshot_date.desc())
            .limit(1)
        )
    ).scalar()
    rows = (
        await session.execute(
            select(KeyTrafficHistory.snapshot_date, KeyTrafficHistory.used_gb, KeyTrafficHistory.limit_gb)
            .where(KeyTrafficHistory.client_id == client_id, KeyTrafficHistory.snapshot_date >= since)
            .order_by(KeyTrafficHistory.snapshot_date.asc())
        )
    ).all()
    if not rows:
        return []

    out: list[dict] = []
    # Достраиваем ведущие дни без данных нулями — чтобы ось охватывала весь выбранный период
    # (7д/30д/90д визуально соответствуют выбору, а не схлопываются к доступной истории).
    pad_day = since
    first_date = rows[0][0]
    while pad_day < first_date:
        out.append({"date": pad_day.isoformat(), "used_gb": 0.0, "limit_gb": None})
        pad_day += _dt.timedelta(days=1)

    prev = float(baseline) if baseline is not None else None
    for d, u, lim in rows:
        cur = float(u) if u is not None else None
        delta = None if cur is None else (0.0 if prev is None else round(max(0.0, cur - prev), 3))
        if cur is not None:
            prev = cur
        out.append({"date": d.isoformat(), "used_gb": delta, "limit_gb": float(lim) if lim is not None else None})
    return out


async def snapshot_all_key_traffic_hourly(session: AsyncSession) -> int:
    """Почасовой снапшот использованного трафика по активным ключам.
    Один ряд на (client_id, час); хранит последние 48 часов (старое чистится)."""
    from panels.remnawave_runtime import fetch_all_remnawave_traffic

    now_ms = int(time.time() * 1000)
    rows = (
        await session.execute(
            select(Key.client_id).where(
                Key.expiry_time > now_ms,
                Key.is_frozen.isnot(True),
                Key.client_id.isnot(None),
            )
        )
    ).all()
    active = [str(cid) for (cid,) in rows if cid]
    if not active:
        return 0

    needed = set(active)
    try:
        used_map = await asyncio.wait_for(fetch_all_remnawave_traffic(session, needed), timeout=_BULK_TIMEOUT_SEC)
    except (TimeoutError, Exception) as exc:
        logger.warning("[TrafficHistory] почасовой bulk-трафик не получен: {}", exc)
        used_map = {}
    if not used_map:
        return 0

    hour = _dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    count = 0
    for cid in active:
        used_bytes = used_map.get(cid)
        if used_bytes is None:
            continue
        used_gb = round(int(used_bytes) / _GB, 3)
        stmt = (
            pg_insert(KeyTrafficHourly)
            .values(client_id=cid, used_gb=used_gb, snapshot_hour=hour)
            .on_conflict_do_update(
                constraint="uq_key_traffic_hourly_client_hour",
                set_={"used_gb": used_gb},
            )
        )
        await session.execute(stmt)
        count += 1

    cutoff = _dt.datetime.utcnow() - _dt.timedelta(hours=48)
    await session.execute(delete(KeyTrafficHourly).where(KeyTrafficHourly.snapshot_hour < cutoff))
    return count


async def get_traffic_history_hourly(session: AsyncSession, client_id: str, hours: int = 24) -> list[dict]:
    since = _dt.datetime.utcnow() - _dt.timedelta(hours=max(1, min(168, hours)))
    baseline = (
        await session.execute(
            select(KeyTrafficHourly.used_gb)
            .where(KeyTrafficHourly.client_id == client_id, KeyTrafficHourly.snapshot_hour < since)
            .order_by(KeyTrafficHourly.snapshot_hour.desc())
            .limit(1)
        )
    ).scalar()
    rows = (
        await session.execute(
            select(KeyTrafficHourly.snapshot_hour, KeyTrafficHourly.used_gb)
            .where(KeyTrafficHourly.client_id == client_id, KeyTrafficHourly.snapshot_hour >= since)
            .order_by(KeyTrafficHourly.snapshot_hour.asc())
        )
    ).all()
    out: list[dict] = []
    prev = float(baseline) if baseline is not None else None
    for h, u in rows:
        cur = float(u) if u is not None else None
        delta = None if cur is None else (0.0 if prev is None else round(max(0.0, cur - prev), 3))
        if cur is not None:
            prev = cur
        out.append({"date": h.isoformat(), "used_gb": delta, "limit_gb": None})
    return out
