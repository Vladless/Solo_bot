import copy

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pytz import timezone
from sqlalchemy import Float, and_, cast, func, insert, literal, or_, select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.client_origin import client_origin
from core.constants import PAYMENT_SYSTEMS_EXCLUDED
from core.redis_cache import cache_delete, cache_delete_pattern, cache_get, cache_key
from database.access.resolution import (
    TelegramId,
    UserId,
    get_user_by_tg_id,
    resolve_user_optional,
    user_id_from_legacy_ref,
    user_ref_cache_key,
)
from database.cache_purge import defer_purge
from database.db import async_session_maker
from database.models import Gift, Payment, SubscriptionEvent, TemporaryData, User
from database.temporary_data import get_temporary_data
from logger import logger


MOSCOW_TZ = timezone("Europe/Moscow")
PAYMENT_CHECKOUT_SNAPSHOT = "payment_checkout_snapshot"
PAYMENT_CHECKOUT_OPERATION = "payment_checkout_operation"


def canonical_payment_provider(provider: str) -> str:
    """Приводит названия одной кассы к общему идентификатору."""
    name = str(provider or "").strip().lower()
    aliases = {"kassa2328": "2328", "yookassa_autopay_web": "yookassa_autopay", "yookassa_sbp": "yookassa"}
    for base, suffixes in {
        "wata": ("ru", "int"),
        "kassai": ("cards", "sbp"),
        "platega": ("cards", "sbp", "crypto"),
        "overpay": ("cards", "sbp"),
        "paritypay": ("sbp",),
    }.items():
        aliases.update({f"{base}_{suffix}": base for suffix in suffixes})
    return aliases.get(name, name)


def require_payment_provider(payment: Payment, provider: str) -> None:
    """Отклоняет уведомление другой кассы для совпавшего номера счёта."""
    if canonical_payment_provider(payment.payment_system) != canonical_payment_provider(provider):
        raise ValueError("Payment belongs to another provider")


def yookassa_refunded_net(payment: Payment) -> Decimal:
    """Возвращает уже списанную при возвратах сумму зачисления."""
    metadata = payment.metadata_ or {}
    refunds = metadata.get("yookassa_refunds") or {}
    refunded_gross = sum((Decimal(str(value)) for value in refunds.values()), Decimal(0))
    net = Decimal(str(payment.amount))
    frozen = metadata.get("yookassa_amounts")
    gross = Decimal(str(frozen["gross"])) if frozen is not None else net
    if not net.is_finite() or not gross.is_finite() or net <= 0 or gross < net:
        raise ValueError("Invalid saved refund amounts")
    if not refunded_gross.is_finite() or not 0 <= refunded_gross <= gross:
        raise ValueError("Invalid saved refund total")
    return (net * refunded_gross / gross).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def apply_yookassa_refund(
    session: AsyncSession,
    user_id: UserId,
    payment_id: str,
    refund_id: str,
    amount,
    *,
    provider: str,
) -> bool:
    """Однократно списывает зачисление при полном или частичном возврате."""
    if not isinstance(user_id, UserId):
        raise ValueError("A canonical users.id is required")
    try:
        refund_amount = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid provider refund amount") from exc
    if (
        not refund_amount.is_finite()
        or refund_amount <= 0
        or not refund_id
        or refund_amount != refund_amount.quantize(Decimal("0.01"))
    ):
        raise ValueError("Refund amount and provider refund ID are required")
    payment = await lock_payment_for_processing(session, payment_id)
    if payment is None or payment.user_id != int(user_id) or payment.status not in ("success", "refunded"):
        raise ValueError("Successful payment does not belong to this billing user")
    require_payment_provider(payment, provider)
    user = await session.scalar(
        select(User).where(User.id == int(user_id)).with_for_update().execution_options(populate_existing=True)
    )
    if user is None:
        raise ValueError("Billing user does not exist")
    metadata = dict(payment.metadata_ or {})
    if metadata.get("owner_deleted") is True:
        raise ValueError("Payment owner has been deleted")
    refunds = dict(metadata.get("yookassa_refunds") or {})
    if refund_id in refunds:
        if Decimal(str(refunds[refund_id])) != refund_amount:
            raise ValueError("Provider refund ID has a different amount")
        return False
    net = Decimal(str(payment.amount))
    frozen = metadata.get("yookassa_amounts")
    gross = Decimal(str(frozen["gross"])) if frozen is not None else net
    if frozen is not None and (Decimal(str(frozen["net"])) != net or gross != net + Decimal(str(frozen["fee"]))):
        raise ValueError("Invalid saved refund amounts")
    previous_net = yookassa_refunded_net(payment)
    total = sum((Decimal(str(value)) for value in refunds.values()), Decimal(0)) + refund_amount
    if total > gross or payment.status == "refunded":
        raise ValueError("Refund total exceeds the original payment amount")
    refunds[refund_id] = str(refund_amount)
    metadata["yookassa_refunds"] = refunds
    payment.metadata_ = metadata
    debit = yookassa_refunded_net(payment) - previous_net
    if total == gross:
        payment.status = "refunded"
    await session.execute(
        update(User).where(User.id == user.id).values(balance=func.coalesce(User.balance, 0) - float(debit))
    )
    keys = [
        cache_key(prefix, ref)
        for ref in (user.id, user.tg_id)
        if ref is not None
        for prefix in ("balance", "profile_data", "user_snapshot")
    ]
    keys.extend(user_ref_cache_key(prefix, user_id) for prefix in ("balance", "profile_data", "user_snapshot"))
    if user.tg_id is not None:
        keys.extend(
            user_ref_cache_key(prefix, ref)
            for ref in (TelegramId(user.tg_id), user.tg_id)
            for prefix in ("balance", "profile_data", "user_snapshot")
        )
    keys.append(_payment_cache_key(payment_id))
    if not defer_purge(session, *keys):
        for key in keys:
            await cache_delete(key)
    await session.flush()
    return True


def _checkout_snapshot(temporary: dict | None) -> dict | None:
    """Убирает из корзины изменяемые реквизиты интерфейса."""
    if not temporary or temporary.get("state") not in {
        "waiting_for_payment",
        "waiting_for_renewal_payment",
        "waiting_for_addons_payment",
        "waiting_for_gift_payment",
    }:
        return None
    volatile = {"message_id", "chat_id", "back_callback", "return_callback", "updated_at", "created_at"}
    return {
        "state": temporary["state"],
        "data": {
            key: copy.deepcopy(value) for key, value in (temporary.get("data") or {}).items() if key not in volatile
        },
    }


async def capture_payment_checkout(session: AsyncSession | None, owner: int, metadata: dict | None = None) -> dict:
    """Привязывает новый счёт к корзине до обращения к кассе."""
    if session is None:
        async with async_session_maker() as own_session:
            return await capture_payment_checkout(own_session, owner, metadata)
    result = copy.deepcopy(metadata or {})
    temporary = None if result.get("payment_flow") == "balance_topup" else await get_temporary_data(session, owner)
    result[PAYMENT_CHECKOUT_SNAPSHOT] = _checkout_snapshot(temporary)
    return result


async def payment_checkout_matches(session: AsyncSession, owner: int, metadata: dict | None) -> bool:
    """Разрешает завершение только той корзины, для которой создан счёт."""
    metadata = metadata or {}
    if PAYMENT_CHECKOUT_SNAPSHOT not in metadata:
        return True
    expected = metadata[PAYMENT_CHECKOUT_SNAPSHOT]
    if not isinstance(expected, dict):
        return False
    uid = await user_id_from_legacy_ref(session, owner)
    if uid is None:
        return False
    row = await session.scalar(
        select(TemporaryData)
        .where(TemporaryData.user_id == int(uid))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    current = {"state": row.state, "data": row.data} if row is not None else None
    return expected == _checkout_snapshot(current)


def _payment_cache_key(pid: str) -> str:
    return cache_key("payment_pending", pid)


async def resolve_payment_creation_owner(session: AsyncSession | None, user_ref: int) -> UserId:
    """Определяет users.id владельца нового счёта."""
    if session is None:
        async with async_session_maker() as own_session:
            return await resolve_payment_creation_owner(own_session, user_ref)
    owner = await user_id_from_legacy_ref(session, user_ref)
    if owner is None:
        raise ValueError("Payment owner was not found")
    return owner


async def _lock_payment_id(session: AsyncSession, payment_id: str, *, wait: bool = True) -> None:
    """Сериализует изменения счёта без ожидания при создании."""
    lock = func.pg_advisory_xact_lock if wait else func.pg_try_advisory_xact_lock
    result = await session.execute(select(lock(func.hashtextextended(payment_id, 0))))
    if not wait and not result.scalar_one():
        raise ValueError("Платёж уже обрабатывается. Повторите создание счёта.")


async def lock_payment_for_processing(session: AsyncSession, payment_id: str) -> Payment | None:
    """Блокирует владельца и платёж для обработки вебхука."""
    await _lock_payment_id(session, payment_id)
    for _ in range(8):
        owner = (
            await session.execute(
                select(Payment.user_id, Payment.tg_id).where(Payment.payment_id == payment_id).limit(1)
            )
        ).first()
        if owner is None:
            return None
        uid = owner.user_id
        if uid is None and owner.tg_id is not None:
            uid = await session.scalar(select(User.id).where(User.tg_id == owner.tg_id))
        if uid is not None:
            locked_uid = await session.scalar(select(User.id).where(User.id == uid).with_for_update())
            if locked_uid is None:
                continue
            current_owner = (
                await session.execute(
                    select(Payment.user_id, Payment.tg_id).where(Payment.payment_id == payment_id).limit(1)
                )
            ).first()
            if current_owner is None:
                return None
            if tuple(current_owner) != tuple(owner):
                continue
        row = (
            await session.execute(
                select(Payment)
                .where(Payment.payment_id == payment_id)
                .limit(1)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is not None and (row.user_id, row.tg_id) != tuple(owner):
            raise RuntimeError("Payment owner changed outside account locking")
        if (
            row is not None
            and row.user_id is None
            and uid is not None
            and row.status not in {"success", "refunded", "chargebacked"}
            and (row.metadata_ or {}).get("owner_deleted") is not True
        ):
            row.user_id = uid
            await session.flush()
        return row
    raise RuntimeError("Payment owner changed repeatedly during processing")


async def resolve_unrecorded_payment_owner(session: AsyncSession, ref: int | None) -> UserId | None:
    """Разрешает владельца незарегистрированного платежа без неоднозначности."""
    if ref is None:
        return None
    if isinstance(ref, UserId):
        owner = await session.scalar(select(User).where(User.id == int(ref)).with_for_update())
    else:
        owner = await session.scalar(select(User).where(User.tg_id == int(ref)).with_for_update())
        conflicting_uid = await session.scalar(select(User.id).where(User.id == int(ref)))
        if owner is not None and conflicting_uid is not None and owner.id != conflicting_uid:
            raise ValueError("Unregistered payment has an ambiguous legacy owner")
    if owner is None:
        raise ValueError("Unregistered payment owner was not found")
    return UserId(owner.id)


async def _notify_admins_payment(
    session: AsyncSession,
    uid: int,
    amount: float,
    payment_system: str,
    metadata: dict | None,
    payment_id: str | None = None,
    internal_id: int | None = None,
) -> None:
    """Уведомляет администраторов о внешней оплате."""
    if str(payment_system or "").lower() in PAYMENT_SYSTEMS_EXCLUDED:
        return
    try:
        from services.admin_notify import notify_payment

        await notify_payment(
            session,
            uid,
            amount=float(amount or 0),
            payment_system=payment_system,
            origin=(metadata or {}).get("origin"),
            payment_id=payment_id,
            internal_id=internal_id,
        )
    except Exception as exc:
        logger.warning("[DB] Уведомление админам об оплате не ушло: {}", exc)


def _with_client_origin(metadata: dict | None) -> dict:
    """Добавляет канал клиента в метаданные платежа."""
    data = dict(metadata or {})
    data.setdefault("origin", client_origin())
    return data


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
    """Сохраняет ожидающий платёж и удаляет устаревший кеш."""
    async with async_session_maker() as session:
        await _lock_payment_id(session, payment_id, wait=False)
        payment = await session.scalar(select(Payment).where(Payment.payment_id == payment_id).limit(1))
        if payment is None:
            internal_id = await add_payment(
                session=session,
                tg_id=tg_id,
                amount=amount,
                payment_system=payment_system,
                status="pending",
                currency=currency,
                payment_id=payment_id,
                metadata=metadata,
                original_amount=original_amount,
            )
            payment = await session.scalar(select(Payment).where(Payment.id == internal_id))
        require_payment_provider(payment, payment_system)
        owner = await resolve_user_optional(session, tg_id)
        if owner is None or payment.user_id != owner.id or Decimal(str(payment.amount)) != Decimal(str(amount)):
            raise ValueError("Existing payment has different owner or amount")
        await session.commit()
    await invalidate_payment_cache(payment_id)
    return True


async def invalidate_payment_cache(payment_id: str) -> None:
    """Удаляет кеш платежа."""
    await cache_delete(_payment_cache_key(payment_id))


async def add_payment(
    session: AsyncSession,
    legacy_user_ref: int | None = None,
    amount: float = 0,
    payment_system: str = "",
    *,
    user_id: int | None = None,
    tg_id: int | None = None,
    status: str = "success",
    currency: str = "RUB",
    payment_id: str | None = None,
    metadata: dict | None = None,
    original_amount: float | None = None,
) -> int:
    """Добавляет платёж с определением владельца."""
    metadata = _with_client_origin(metadata)
    ref = UserId(user_id) if user_id is not None else next((v for v in (tg_id, legacy_user_ref) if v is not None), None)
    if ref is None:
        raise ValueError("add_payment: не указан клиент")
    u = await resolve_user_optional(session, ref)
    if u is None:
        raise ValueError(f"user not found for payment: {ref}")
    if payment_id:
        await _lock_payment_id(session, payment_id, wait=False)
        existing = await session.scalar(select(Payment).where(Payment.payment_id == payment_id).limit(1))
        if existing is not None:
            require_payment_provider(existing, payment_system)
            if existing.user_id != u.id or Decimal(str(existing.amount)) != Decimal(str(amount)):
                raise ValueError("Existing payment has different owner or amount")
            return int(existing.id)
    if (
        status == "pending"
        and PAYMENT_CHECKOUT_SNAPSHOT not in metadata
        and str(payment_system).lower() not in {"2328", "yookassa_autopay", "yookassa_autopay_web"}
    ):
        metadata = await capture_payment_checkout(session, UserId(u.id), metadata)
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
    await cache_delete_pattern(cache_key("my_payments", u.id, "*"))
    logger.info(
        f"Добавлен платёж id={internal_id}: user_id={u.id}, amount={amount}, system={payment_system}, status={status}"
    )
    if status == "success":
        await _notify_admins_payment(
            session,
            int(u.id),
            amount,
            payment_system,
            metadata,
            payment_id=payment_id,
            internal_id=int(internal_id),
        )
    return internal_id


def _balance_activity_union(uid: int | None, tg_id: int | None, success_only: bool):
    if uid is not None:
        tg_id = select(User.tg_id).where(User.id == uid).scalar_subquery()
        uid = select(User.id).where(User.id == uid).scalar_subquery()
    payment_refs = []
    if uid is not None:
        payment_refs.append(Payment.user_id == uid)
    if tg_id is not None:
        payment_refs.append(and_(Payment.user_id.is_(None), Payment.tg_id == tg_id))
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
        gift_refs.append(and_(Gift.sender_user_id.is_(None), Gift.sender_tg_id == tg_id))
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
        spend_refs.append(and_(SubscriptionEvent.user_id.is_(None), SubscriptionEvent.tg_id == tg_id))
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
    if uid is None and tg_id is not None:
        user = await get_user_by_tg_id(session, tg_id)
        uid = user.id if user is not None else None
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
    if uid is None and tg_id is not None:
        user = await get_user_by_tg_id(session, tg_id)
        uid = user.id if user is not None else None
    u = _balance_activity_union(uid, tg_id, success_only)
    stmt = select(u).order_by(u.c.created_at.desc()).offset(offset).limit(limit)
    return (await session.execute(stmt)).all()


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
    base = dict(payment.metadata_ or {})
    if new_status == "success" and "status_changed_at" not in base:
        base["status_changed_at"] = datetime.utcnow().replace(tzinfo=None).isoformat()
    if metadata_patch:
        base.update({
            key: value
            for key, value in metadata_patch.items()
            if key not in {PAYMENT_CHECKOUT_SNAPSHOT, PAYMENT_CHECKOUT_OPERATION, "yookassa_amounts"}
        })
    if base:
        payment.metadata_ = base

    await session.flush()
    logger.info(f"Статус платежа id={internal_id} изменён на {new_status}")
    if new_status == "success" and payment.user_id is not None:
        await _notify_admins_payment(
            session,
            int(payment.user_id),
            float(payment.amount or 0),
            str(payment.payment_system or ""),
            payment.metadata_,
            payment_id=payment.payment_id,
            internal_id=int(payment.id),
        )
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
        "tg_id": UserId(payment.user_id) if payment.user_id is not None else payment.tg_id,
        "user_id": UserId(payment.user_id) if payment.user_id is not None else None,
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
    """Находит платёж по ID с приоритетом данных БД."""
    result = await session.execute(select(Payment).where(Payment.payment_id == pid).limit(1))
    payment = result.scalar_one_or_none()
    if payment is None:
        cached = await cache_get(_payment_cache_key(pid))
        if cached is None:
            return None
        return {
            "id": None,
            "tg_id": UserId(cached["user_id"]) if cached.get("user_id") is not None else cached.get("tg_id"),
            "user_id": UserId(cached["user_id"]) if cached.get("user_id") is not None else None,
            "amount": cached["amount"],
            "currency": cached.get("currency", "RUB"),
            "status": cached.get("status", "pending"),
            "payment_system": cached["payment_system"],
            "payment_id": cached["payment_id"],
            "created_at": None,
            "metadata": cached.get("metadata"),
            "original_amount": cached.get("original_amount"),
        }
    return {
        "id": payment.id,
        "tg_id": UserId(payment.user_id) if payment.user_id is not None else payment.tg_id,
        "user_id": UserId(payment.user_id) if payment.user_id is not None else None,
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
    """Считает успешные внешние платежи клиента."""
    result = await session.execute(
        select(func.count())
        .select_from(Payment)
        .where(
            Payment.user_id == int(user_id),
            func.lower(Payment.status) == "success",
            Payment.payment_system.notin_(PAYMENT_SYSTEMS_EXCLUDED),
        )
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
                func.upper(func.coalesce(Payment.payment_system, "")).notin_((
                    "YOOKASSA_AUTOPAY",
                    "YOOKASSA_AUTOPAY_WEB",
                    "2328",
                )),
            )
        )
        .values(status="cancelled")
    )
    res = await session.execute(stmt)
    affected = res.rowcount or 0
    return affected
