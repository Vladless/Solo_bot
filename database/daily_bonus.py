from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DailyBonusClaim, Key, User


_LOCK_NAMESPACE = 0x62_6F_6E_75  # "bonu"


async def lock_user_bonus(session: AsyncSession, user_id: int) -> None:
    """Транзакционная блокировка выдачи бонуса пользователю — защита от двойного клика."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :uid)"),
        {"ns": _LOCK_NAMESPACE, "uid": int(user_id)},
    )


async def get_last_claim(session: AsyncSession, user_id: int) -> DailyBonusClaim | None:
    result = await session.execute(
        select(DailyBonusClaim)
        .where(DailyBonusClaim.user_id == int(user_id))
        .order_by(DailyBonusClaim.created_at.desc(), DailyBonusClaim.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def count_claims_since(session: AsyncSession, user_id: int, since: datetime) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(DailyBonusClaim)
        .where(DailyBonusClaim.user_id == int(user_id), DailyBonusClaim.created_at >= since)
    )
    return int(result.scalar() or 0)


async def first_claim_since(session: AsyncSession, user_id: int, since: datetime) -> datetime | None:
    result = await session.execute(
        select(func.min(DailyBonusClaim.created_at)).where(
            DailyBonusClaim.user_id == int(user_id), DailyBonusClaim.created_at >= since
        )
    )
    return result.scalar_one_or_none()


async def total_claimed(session: AsyncSession, user_id: int) -> float:
    result = await session.execute(
        select(func.coalesce(func.sum(DailyBonusClaim.amount), 0.0)).where(DailyBonusClaim.user_id == int(user_id))
    )
    return float(result.scalar() or 0.0)


async def insert_claim(
    session: AsyncSession,
    user_id: int,
    tg_id: int | None,
    amount: float,
    streak: int,
    source: str,
    claimed_at: datetime,
) -> DailyBonusClaim:
    claim = DailyBonusClaim(
        user_id=int(user_id),
        tg_id=int(tg_id) if tg_id is not None else None,
        amount=round(float(amount), 2),
        streak=int(streak),
        source=source[:16],
        created_at=claimed_at,
    )
    session.add(claim)
    await session.flush()
    return claim


async def get_user_created_at(session: AsyncSession, user_id: int) -> datetime | None:
    result = await session.execute(select(User.created_at).where(User.id == int(user_id)))
    return result.scalar_one_or_none()


async def has_active_subscription(session: AsyncSession, user_id: int) -> bool:
    now_ms = int(datetime.utcnow().timestamp() * 1000)
    result = await session.execute(
        select(func.count())
        .select_from(Key)
        .where(Key.user_id == int(user_id), Key.expiry_time > now_ms, Key.is_frozen.is_(False))
    )
    return int(result.scalar() or 0) > 0
