from datetime import datetime, timedelta

from pytz import timezone
from sqlalchemy import Float, and_, cast, func, insert, literal, or_, select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_get, cache_key, cache_set
from database.access.resolution import resolve_user_optional
from database.models import Gift, Payment, SubscriptionEvent
from logger import logger
from settings.cache_config import PAYMENT_PENDING_CACHE_TTL_SEC


MOSCOW_TZ = timezone("Europe/Moscow")


def _payment_cache_key(pid: str) -> str:
    return cache_key("payment_pending", pid)


async def register_pending_payment(
    payment_id: str,
    tg_id: int,
    amount: float,
    payment_system: str,
    *,
    currency: str = "RUB",
    metadata: dict | None = None,
    original_amount: float | None = None,
) -> bool:
    """Регистрирует ожидающий платёж только в Redis. В БД пишем при success/fail из вебхука."""
    data = {
        "tg_id": tg_id,
        "amount": amount,
        "currency": currency,
        "status": "pending",
        "payment_system": payment_system,
        "payment_id": payment_id,
        "metadata": metadata,
        "original_amount": original_amount,
    }
    ok = await cache_set(_payment_cache_key(payment_id), data, PAYMENT_PENDING_CACHE_TTL_SEC)
    if ok:
        logger.debug(f"[Payments] Pending в кэше: payment_id={payment_id}, tg_id={tg_id}")
    return ok


async def invalidate_payment_cache(payment_id: str) -> None:
    """Вызвать после сохранения платежа в БД (success/fail) из вебхука."""
    await cache_delete(_payment_cache_key(payment_id))


async def add_payment(
    session: AsyncSession,
    legacy_user_ref: int | None = None,
    amount: float = 0,
    payment_system: str = "",
    *,
    tg_id: int | None = None,
    status: str = "success",
    currency: str = "RUB",
    payment_id: str | None = None,
    metadata: dict | None = None,
    original_amount: float | None = None,
) -> int:
    if legacy_user_ref is None:
        legacy_user_ref = tg_id
    if legacy_user_ref is None:
        raise ValueError("legacy_user_ref is required for payment")
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        raise ValueError(f"user not found for payment: {legacy_user_ref}")
    now_moscow = datetime.now(MOSCOW_TZ).replace(tzinfo=None)
    stmt = (
        insert(Payment)
        .values(
            user_id=u.id,
            tg_id=u.tg_id,
            amount=amount,
            payment_system=payment_system,
            status=status,
            created_at=now_moscow,
            currency=currency,
            payment_id=payment_id,
            metadata_=metadata,
            original_amount=original_amount,
        )
        .returning(Payment.id)
    )
    result = await session.execute(stmt)
    internal_id = result.scalar_one()
    from core.redis_cache import cache_delete_pattern

    await cache_delete_pattern(cache_key("my_payments", u.id, "*"))
    logger.info(
        f"Добавлен платёж id={internal_id}: user_id={u.id}, amount={amount}, system={payment_system}, status={status}"
    )
    return internal_id


def _balance_activity_union(uid: int | None, tg_id: int | None, success_only: bool):
    payment_refs = []
    if uid is not None:
        payment_refs.append(Payment.user_id == uid)
    if tg_id is not None:
        payment_refs.append(Payment.tg_id == tg_id)
    p = select(
        Payment.created_at.label("created_at"),
        cast(Payment.amount, Float).label("amount"),
        literal("payment").label("kind"),
        Payment.payment_system.label("system"),
        Payment.status.label("status"),
        Payment.payment_id.label("ref"),
    ).where(or_(*payment_refs) if payment_refs else literal(False))
    if success_only:
        p = p.where(Payment.status == "success")

    gift_refs = []
    if uid is not None:
        gift_refs.append(Gift.sender_user_id == uid)
    if tg_id is not None:
        gift_refs.append(Gift.sender_tg_id == tg_id)
    g = select(
        Gift.created_at.label("created_at"),
        cast(-func.coalesce(Gift.selected_price_rub, 0), Float).label("amount"),
        literal("gift").label("kind"),
        literal("gift").label("system"),
        literal("success").label("status"),
        Gift.gift_id.label("ref"),
    ).where(or_(*gift_refs) if gift_refs else literal(False))

    spend_refs = []
    if uid is not None:
        spend_refs.append(SubscriptionEvent.user_id == uid)
    if tg_id is not None:
        spend_refs.append(SubscriptionEvent.tg_id == tg_id)
    s = (
        select(
            SubscriptionEvent.created_at.label("created_at"),
            cast(-SubscriptionEvent.price_rub, Float).label("amount"),
            literal("spend").label("kind"),
            SubscriptionEvent.event_type.label("system"),
            literal("success").label("status"),
            SubscriptionEvent.client_id.label("ref"),
        )
        .where(or_(*spend_refs) if spend_refs else literal(False))
        .where(SubscriptionEvent.event_type.in_(("created", "renewed", "addons")))
        .where(SubscriptionEvent.price_rub > 0)
        .where(or_(SubscriptionEvent.source.is_(None), SubscriptionEvent.source != "backfill"))
    )

    return union_all(p, g, s).subquery()


async def count_balance_activity(
    session: AsyncSession, *, uid: int | None, tg_id: int | None, success_only: bool = False
) -> int:
    u = _balance_activity_union(uid, tg_id, success_only)
    return int((await session.scalar(select(func.count()).select_from(u))) or 0)


async def get_balance_activity(
    session: AsyncSession,
    *,
    uid: int | None,
    tg_id: int | None,
    limit: int,
    offset: int = 0,
    success_only: bool = False,
) -> list:
    u = _balance_activity_union(uid, tg_id, success_only)
    stmt = select(u).order_by(u.c.created_at.desc()).offset(offset).limit(limit)
    return (await session.execute(stmt)).all()


async def get_last_payments(
    session: AsyncSession,
    legacy_user_ref: int,
    limit: int = 3,
    statuses: list[str] | None = None,
):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return []
    query = select(Payment).where(Payment.user_id == u.id)

    if statuses:
        query = query.where(Payment.status.in_(statuses))

    query = query.order_by(Payment.created_at.desc()).limit(limit)

    result = await session.execute(query)
    payments = result.scalars().all()
    return [
        {
            "id": p.id,
            "tg_id": p.user_id,
            "user_id": p.user_id,
            "amount": p.amount,
            "currency": p.currency,
            "status": p.status,
            "payment_system": p.payment_system,
            "payment_id": p.payment_id,
            "created_at": p.created_at,
            "metadata": p.metadata_,
            "original_amount": p.original_amount,
        }
        for p in payments
    ]


async def get_payment_by_id(session: AsyncSession, internal_id: int) -> dict | None:
    result = await session.execute(select(Payment).where(Payment.id == internal_id).limit(1))
    payment = result.scalar_one_or_none()
    if not payment:
        return None
    return {
        "id": payment.id,
        "tg_id": payment.user_id,
        "user_id": payment.user_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "payment_system": payment.payment_system,
        "payment_id": payment.payment_id,
        "created_at": payment.created_at,
        "metadata": payment.metadata_,
        "original_amount": payment.original_amount,
    }


async def update_payment_status(
    session: AsyncSession,
    internal_id: int,
    new_status: str,
    *,
    payment_id: str | None = None,
    metadata_patch: dict | None = None,
) -> bool:
    result = await session.execute(select(Payment).where(Payment.id == internal_id).limit(1))
    payment = result.scalar_one_or_none()
    if not payment:
        logger.info(f"Не удалось сменить статус: платёж id={internal_id} не найден")
        return False

    payment.status = new_status
    if payment_id is not None:
        payment.payment_id = payment_id
    base = payment.metadata_ or {}
    if new_status == "success" and "status_changed_at" not in base:
        base["status_changed_at"] = datetime.utcnow().replace(tzinfo=None).isoformat()
    if metadata_patch:
        base.update(metadata_patch)
    if base:
        payment.metadata_ = base

    await session.flush()
    logger.info(f"Статус платежа id={internal_id} изменён на {new_status}")
    return True


async def get_payment_from_db_by_payment_id(session: AsyncSession, pid: str) -> dict | None:
    if not str(pid or "").strip():
        return None
    result = await session.execute(select(Payment).where(Payment.payment_id == pid).limit(1))
    payment = result.scalar_one_or_none()
    if not payment:
        return None
    return {
        "id": payment.id,
        "tg_id": payment.user_id,
        "user_id": payment.user_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "payment_system": payment.payment_system,
        "payment_id": payment.payment_id,
        "created_at": payment.created_at,
        "metadata": payment.metadata_,
        "original_amount": payment.original_amount,
    }


async def get_payment_by_payment_id(session: AsyncSession, pid: str) -> dict | None:
    """Сначала Redis (pending), затем БД. Из кэша возвращается запись без id — вебхук делает add_payment."""
    cached = await cache_get(_payment_cache_key(pid))
    if cached is not None:
        return {
            "id": None,
            "tg_id": cached["tg_id"],
            "amount": cached["amount"],
            "currency": cached.get("currency", "RUB"),
            "status": cached.get("status", "pending"),
            "payment_system": cached["payment_system"],
            "payment_id": cached["payment_id"],
            "created_at": None,
            "metadata": cached.get("metadata"),
            "original_amount": cached.get("original_amount"),
        }
    result = await session.execute(select(Payment).where(Payment.payment_id == pid).limit(1))
    payment = result.scalar_one_or_none()
    if not payment:
        return None
    return {
        "id": payment.id,
        "tg_id": payment.user_id,
        "user_id": payment.user_id,
        "amount": payment.amount,
        "currency": payment.currency,
        "status": payment.status,
        "payment_system": payment.payment_system,
        "payment_id": payment.payment_id,
        "created_at": payment.created_at,
        "metadata": payment.metadata_,
        "original_amount": payment.original_amount,
    }


async def count_successful_payments(session: AsyncSession, user_id: int) -> int:
    """Сколько успешных платежей у пользователя (по internal user id).

    Используется для проверки "новый пользователь" в купонных правилах.
    """
    result = await session.execute(
        select(func.count())
        .select_from(Payment)
        .where(Payment.user_id == int(user_id), func.lower(Payment.status) == "success")
    )
    return int(result.scalar() or 0)


async def cancel_expired_pending_payments(session: AsyncSession) -> int:
    cutoff = datetime.now(MOSCOW_TZ).replace(tzinfo=None) - timedelta(minutes=60)
    stmt = (
        update(Payment)
        .where(
            and_(
                Payment.status.in_(("pending", "issued", "processing", "awaiting_choice")),
                Payment.created_at < cutoff,
            )
        )
        .values(status="cancelled")
    )
    res = await session.execute(stmt)
    affected = res.rowcount or 0
    return affected


async def get_all_payments(
    session: AsyncSession,
    legacy_user_ref: int,
    statuses: list[str] | None = None,
) -> list[dict]:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return []
    query = select(Payment).where(Payment.user_id == u.id)

    if statuses:
        query = query.where(Payment.status.in_(statuses))

    query = query.order_by(Payment.created_at.desc())

    result = await session.execute(query)
    payments = result.scalars().all()
    return [
        {
            "id": p.id,
            "tg_id": p.user_id,
            "user_id": p.user_id,
            "amount": p.amount,
            "currency": p.currency,
            "status": p.status,
            "payment_system": p.payment_system,
            "payment_id": p.payment_id,
            "created_at": p.created_at,
            "metadata": p.metadata_,
            "original_amount": p.original_amount,
        }
        for p in payments
    ]
