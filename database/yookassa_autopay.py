import json
import uuid

from collections import defaultdict
from datetime import datetime

from sqlalchemy import delete, func, inspect, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.access.resolution import UserId
from database.models import (
    Identity,
    Key,
    User,
    YooKassaAutopayAttempt,
    YooKassaLog,
    YooKassaPayment,
    YooKassaSavedCard,
    YooKassaSubscription,
)
from database.payments import apply_yookassa_refund


UNFINISHED_STATUSES = ("prepared", "unknown", "pending", "succeeded", "manual_review")
ATTEMPT_STATUSES = (*UNFINISHED_STATUSES, "canceled", "applied")


async def _owner(session: AsyncSession, user_id: int, *, lock: bool = False) -> User:
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError("A canonical users.id is required")
    stmt = select(User).where(User.id == int(user_id)).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    user = await session.scalar(stmt)
    if user is None:
        raise ValueError("Billing user does not exist")
    return user


async def has_legacy_autopay_usage(session: AsyncSession) -> bool:
    connection = await session.connection()
    for model in (YooKassaSavedCard, YooKassaSubscription, YooKassaPayment):
        exists = await connection.run_sync(lambda conn, model=model: inspect(conn).has_table(model.__tablename__))
        if exists and await session.scalar(select(literal(1)).select_from(model).limit(1)):
            return True
    return False


async def get_owner(session: AsyncSession, user_id: int, lock: bool = False) -> User:
    return await _owner(session, user_id, lock=lock)


async def get_owned_key(session: AsyncSession, user_id: int, client_id: str, lock: bool = False) -> Key | None:
    if lock:
        await _owner(session, user_id, lock=True)
    stmt = select(Key).where(Key.user_id == int(user_id), Key.client_id == client_id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(stmt)


async def get_owner_email(session: AsyncSession, user_id: int) -> str | None:
    return await session.scalar(
        select(Identity.email).join(User, User.identity_id == Identity.id).where(User.id == int(user_id))
    )


async def list_cards(session: AsyncSession, user_id: int) -> list[YooKassaSavedCard]:
    return list(
        (
            await session.scalars(
                select(YooKassaSavedCard)
                .where(YooKassaSavedCard.user_id == int(user_id), YooKassaSavedCard.deleted_at.is_(None))
                .order_by(YooKassaSavedCard.created_at.desc(), YooKassaSavedCard.id)
            )
        ).all()
    )


async def get_owned_card(session: AsyncSession, user_id: int, card_id: str) -> YooKassaSavedCard | None:
    return await session.scalar(
        select(YooKassaSavedCard).where(
            YooKassaSavedCard.user_id == int(user_id),
            YooKassaSavedCard.id == card_id,
            YooKassaSavedCard.deleted_at.is_(None),
        )
    )


async def get_active_card(session: AsyncSession, user_id: int) -> YooKassaSavedCard | None:
    return await session.scalar(
        select(YooKassaSavedCard)
        .where(
            YooKassaSavedCard.user_id == int(user_id),
            YooKassaSavedCard.is_active.is_(True),
            YooKassaSavedCard.deleted_at.is_(None),
        )
        .order_by(YooKassaSavedCard.created_at.desc(), YooKassaSavedCard.id)
        .limit(1)
    )


async def save_verified_card(
    session: AsyncSession,
    user_id: int,
    card_id: str,
    card_type: str | None = None,
    card_mask: str | None = None,
    bank_name: str | None = None,
) -> YooKassaSavedCard:
    user = await _owner(session, user_id, lock=True)
    card = await session.scalar(select(YooKassaSavedCard).where(YooKassaSavedCard.id == card_id).with_for_update())
    if card is not None and card.user_id != user.id:
        raise ValueError("Saved payment method belongs to another or unresolved account")
    if card is not None and card.deleted_at is not None:
        return card
    await session.execute(update(YooKassaSavedCard).where(YooKassaSavedCard.user_id == user.id).values(is_active=False))
    if card is None:
        card = YooKassaSavedCard(id=card_id, user_id=user.id, tg_id=user.tg_id)
        session.add(card)
    card.card_type, card.card_mask, card.bank_name, card.is_active = card_type, card_mask, bank_name, True
    await session.flush()
    return card


async def activate_card(session: AsyncSession, user_id: int, card_id: str) -> bool:
    await _owner(session, user_id, lock=True)
    card = await get_owned_card(session, user_id, card_id)
    if card is None:
        return False
    await session.execute(update(YooKassaSavedCard).where(YooKassaSavedCard.user_id == user_id).values(is_active=False))
    card.is_active = True
    await session.flush()
    return True


async def delete_card(session: AsyncSession, user_id: int, card_id: str) -> bool:
    await _owner(session, user_id, lock=True)
    card = await get_owned_card(session, user_id, card_id)
    if card is None:
        return False
    was_active = card.is_active
    await session.execute(
        update(YooKassaAutopayAttempt)
        .where(
            YooKassaAutopayAttempt.subscription_id.in_(
                select(YooKassaSubscription.id).where(
                    YooKassaSubscription.user_id == user_id, YooKassaSubscription.card_id == card_id
                )
            )
        )
        .values(consent_canceled=True)
    )
    await session.execute(
        update(YooKassaSubscription)
        .where(YooKassaSubscription.user_id == user_id, YooKassaSubscription.card_id == card_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    card.is_active, card.deleted_at = False, datetime.utcnow()
    await session.flush()
    if was_active:
        remaining = await session.scalar(
            select(YooKassaSavedCard)
            .where(YooKassaSavedCard.user_id == user_id, YooKassaSavedCard.deleted_at.is_(None))
            .order_by(YooKassaSavedCard.created_at.desc(), YooKassaSavedCard.id)
            .limit(1)
        )
        if remaining is not None:
            remaining.is_active = True
    await session.flush()
    return True


async def list_subscriptions(session: AsyncSession, user_id: int) -> list[YooKassaSubscription]:
    return list(
        (
            await session.scalars(
                select(YooKassaSubscription)
                .where(YooKassaSubscription.user_id == int(user_id))
                .order_by(YooKassaSubscription.id.desc())
            )
        ).all()
    )


async def get_owned_subscription(session: AsyncSession, user_id: int, client_id: str) -> YooKassaSubscription | None:
    return await session.scalar(
        select(YooKassaSubscription)
        .where(
            YooKassaSubscription.user_id == int(user_id),
            YooKassaSubscription.client_id == client_id,
            YooKassaSubscription.is_active.is_(True),
        )
        .order_by(YooKassaSubscription.id.desc())
        .limit(1)
    )


async def upsert_subscription(
    session: AsyncSession,
    user_id: int,
    client_id: str,
    card_id: str,
    amount: float,
    next_payment_date: datetime,
    tariff_id: int | None = None,
    consent: bool = False,
    period_days: int | None = None,
    accepted_gross_amount: float | None = None,
) -> YooKassaSubscription:
    if not consent:
        raise ValueError("Recurring billing requires explicit consent")
    if period_days is not None and (
        not isinstance(period_days, int) or isinstance(period_days, bool) or period_days <= 0
    ):
        raise ValueError("Recurring billing period must be positive")
    user = await _owner(session, user_id, lock=True)
    key = await session.get(Key, (user.id, client_id))
    card = await get_owned_card(session, user.id, card_id)
    if key is None or card is None:
        raise ValueError("Key and saved payment method must belong to the billing user")
    unfinished = await session.scalar(
        select(YooKassaAutopayAttempt.id)
        .join(YooKassaSubscription, YooKassaSubscription.id == YooKassaAutopayAttempt.subscription_id)
        .where(
            YooKassaSubscription.user_id == user.id,
            YooKassaSubscription.client_id == client_id,
            YooKassaAutopayAttempt.status.in_(UNFINISHED_STATUSES),
        )
        .limit(1)
    )
    if unfinished:
        raise ValueError("Recurring payment is unfinished")
    await session.execute(
        update(YooKassaSubscription)
        .where(
            YooKassaSubscription.user_id == user.id,
            YooKassaSubscription.client_id == client_id,
            YooKassaSubscription.is_active.is_(True),
        )
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    subscription = YooKassaSubscription(
        user_id=user.id,
        tg_id=user.tg_id,
        client_id=client_id,
        card_id=card_id,
        tariff_id=tariff_id,
        period_days=period_days,
        amount=amount,
        accepted_gross_amount=accepted_gross_amount,
        next_payment_date=next_payment_date,
        retry_count=0,
        is_active=True,
        is_processing=False,
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def cancel_subscription(session: AsyncSession, user_id: int, subscription_id: int) -> bool:
    await _owner(session, user_id, lock=True)
    await session.execute(
        update(YooKassaAutopayAttempt)
        .where(YooKassaAutopayAttempt.user_id == user_id, YooKassaAutopayAttempt.subscription_id == subscription_id)
        .values(consent_canceled=True)
    )
    result = await session.execute(
        update(YooKassaSubscription)
        .where(YooKassaSubscription.user_id == user_id, YooKassaSubscription.id == subscription_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    return bool(result.rowcount)


async def list_due_subscriptions(session: AsyncSession, now: datetime | None = None) -> list[YooKassaSubscription]:
    return list(
        (
            await session.scalars(
                select(YooKassaSubscription)
                .where(
                    YooKassaSubscription.user_id.is_not(None),
                    YooKassaSubscription.is_active.is_(True),
                    YooKassaSubscription.next_payment_date <= (now or datetime.utcnow()),
                )
                .order_by(YooKassaSubscription.next_payment_date, YooKassaSubscription.id)
            )
        ).all()
    )


async def list_unfinished_attempts(session: AsyncSession, user_id: int | None = None) -> list[YooKassaAutopayAttempt]:
    stmt = select(YooKassaAutopayAttempt).where(YooKassaAutopayAttempt.status.in_(UNFINISHED_STATUSES))
    if user_id is not None:
        stmt = stmt.where(YooKassaAutopayAttempt.user_id == int(user_id))
    return list(
        (await session.scalars(stmt.order_by(YooKassaAutopayAttempt.created_at, YooKassaAutopayAttempt.id))).all()
    )


async def get_attempt(session: AsyncSession, attempt_id: str) -> YooKassaAutopayAttempt | None:
    return await session.get(YooKassaAutopayAttempt, attempt_id)


async def get_subscription(
    session: AsyncSession, subscription_id: int, lock: bool = False
) -> YooKassaSubscription | None:
    if not lock:
        return await session.get(YooKassaSubscription, subscription_id)
    for _ in range(3):
        owner_id = await session.scalar(
            select(YooKassaSubscription.user_id).where(YooKassaSubscription.id == subscription_id)
        )
        if owner_id is None:
            return None
        try:
            await _owner(session, owner_id, lock=True)
        except ValueError:
            continue
        subscription = await session.scalar(
            select(YooKassaSubscription)
            .where(YooKassaSubscription.id == subscription_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if subscription is None or subscription.user_id == owner_id:
            return subscription
    raise ValueError("Recurring subscription owner changed concurrently")


async def get_open_subscription_attempt(session: AsyncSession, subscription_id: int) -> YooKassaAutopayAttempt | None:
    return await session.scalar(
        select(YooKassaAutopayAttempt)
        .where(
            YooKassaAutopayAttempt.subscription_id == subscription_id,
            YooKassaAutopayAttempt.status.in_(UNFINISHED_STATUSES),
        )
        .order_by(YooKassaAutopayAttempt.created_at)
        .limit(1)
    )


async def find_open_checkout_attempt(
    session: AsyncSession, user_id: int, fingerprint: str | None
) -> YooKassaAutopayAttempt | None:
    conditions = [YooKassaAutopayAttempt.payment_id.is_(None)]
    if fingerprint is not None:
        conditions.append(YooKassaAutopayAttempt.intent["checkout_fingerprint"].astext == str(fingerprint))
    return await session.scalar(
        select(YooKassaAutopayAttempt)
        .where(
            YooKassaAutopayAttempt.user_id == int(user_id),
            YooKassaAutopayAttempt.subscription_id.is_(None),
            YooKassaAutopayAttempt.status.in_(UNFINISHED_STATUSES),
            or_(*conditions),
        )
        .order_by(YooKassaAutopayAttempt.created_at)
        .limit(1)
    )


async def get_attempt_by_payment_id(session: AsyncSession, payment_id: str) -> YooKassaAutopayAttempt | None:
    return await session.scalar(select(YooKassaAutopayAttempt).where(YooKassaAutopayAttempt.payment_id == payment_id))


async def lock_attempt(session: AsyncSession, attempt_id: str) -> YooKassaAutopayAttempt | None:
    for _ in range(3):
        owner_id = await session.scalar(
            select(YooKassaAutopayAttempt.user_id).where(YooKassaAutopayAttempt.id == attempt_id)
        )
        if owner_id is None:
            return await session.scalar(
                select(YooKassaAutopayAttempt)
                .where(YooKassaAutopayAttempt.id == attempt_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        try:
            await _owner(session, owner_id, lock=True)
        except ValueError:
            continue
        attempt = await session.scalar(
            select(YooKassaAutopayAttempt)
            .where(YooKassaAutopayAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if attempt is None or attempt.user_id == owner_id:
            return attempt
    raise ValueError("Recurring payment owner changed concurrently")


async def prepare_attempt(
    session: AsyncSession,
    user_id: int,
    *,
    payload: dict,
    intent: dict,
    idempotency_key: str | None = None,
    subscription_id: int | None = None,
    attempt_id: str | None = None,
    allow_inactive_subscription: bool = False,
) -> YooKassaAutopayAttempt:
    user = await _owner(session, user_id, lock=True)
    if not isinstance(payload, dict) or not isinstance(intent, dict):
        raise ValueError("Payment payload and intent must be dictionaries")
    if attempt_id is not None:
        attempt_id = str(uuid.UUID(attempt_id))
    if idempotency_key is not None or attempt_id is not None:
        requested_key = idempotency_key or attempt_id
        existing = await session.scalar(
            select(YooKassaAutopayAttempt).where(YooKassaAutopayAttempt.idempotency_key == requested_key)
        )
        if existing is not None:
            if (
                existing.user_id != user.id
                or existing.payload != payload
                or existing.intent != intent
                or existing.subscription_id != subscription_id
            ):
                raise ValueError("Idempotency key has another payment payload or owner")
            return existing
    if subscription_id is not None:
        subscription = await session.scalar(
            select(YooKassaSubscription)
            .where(YooKassaSubscription.id == subscription_id, YooKassaSubscription.user_id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if subscription is None or (not subscription.is_active and not allow_inactive_subscription):
            raise ValueError("Recurring subscription is inactive or belongs to another account")
        existing = await session.scalar(
            select(YooKassaAutopayAttempt)
            .where(
                YooKassaAutopayAttempt.subscription_id == subscription_id,
                YooKassaAutopayAttempt.status.in_(UNFINISHED_STATUSES),
            )
            .with_for_update()
        )
        if existing is not None:
            return existing
        subscription.is_processing = True
        subscription.last_attempt_at = datetime.utcnow()
    elif not intent.get("legacy"):
        existing = await find_open_checkout_attempt(session, user.id, intent.get("checkout_fingerprint"))
        if existing is not None:
            return existing
    attempt_id = attempt_id or str(uuid.uuid4())
    attempt = YooKassaAutopayAttempt(
        id=attempt_id,
        user_id=user.id,
        subscription_id=subscription_id,
        idempotency_key=idempotency_key or attempt_id,
        payload=json.loads(json.dumps(payload)),
        intent=json.loads(json.dumps(intent)),
        status="prepared",
        consent_canceled=bool(subscription_id is not None and not subscription.is_active),
    )
    session.add(attempt)
    await session.flush()
    return attempt


async def update_attempt_result(
    session: AsyncSession,
    attempt_id: str,
    *,
    status: str,
    payment_id: str | None = None,
    confirmation_url: str | None = None,
) -> YooKassaAutopayAttempt:
    if status not in ATTEMPT_STATUSES:
        raise ValueError("Unknown recurring payment status")
    attempt = await lock_attempt(session, attempt_id)
    if attempt is None:
        raise ValueError("Recurring payment attempt does not exist")
    if payment_id is not None:
        if attempt.payment_id is not None and attempt.payment_id != payment_id:
            raise ValueError("Attempt already has a different provider payment")
        attempt.payment_id = payment_id
    if attempt.status == "applied" or (attempt.status == "canceled" and status not in ("canceled", "manual_review")):
        return attempt
    if attempt.status == "succeeded" and status in ("prepared", "unknown", "pending"):
        return attempt
    attempt.status = status
    if confirmation_url is not None:
        attempt.confirmation_url = confirmation_url
    if status == "canceled" and attempt.subscription_id is not None:
        await session.execute(
            update(YooKassaSubscription)
            .where(YooKassaSubscription.id == attempt.subscription_id)
            .values(is_processing=False, last_payment_id=None)
        )
    await session.flush()
    return attempt


async def finish_subscription_cycle(session: AsyncSession, subscription_id: int, next_payment_date: datetime) -> None:
    owner_id = await session.scalar(
        select(YooKassaSubscription.user_id).where(YooKassaSubscription.id == subscription_id)
    )
    if owner_id is None:
        raise ValueError("Recurring subscription has no billing owner")
    await _owner(session, owner_id, lock=True)
    await session.execute(
        update(YooKassaSubscription)
        .where(YooKassaSubscription.id == subscription_id, YooKassaSubscription.user_id == owner_id)
        .values(
            next_payment_date=next_payment_date,
            retry_count=0,
            last_attempt_at=None,
            is_processing=False,
            last_payment_id=None,
        )
    )


async def fail_attempt_subscription(session: AsyncSession, subscription_id: int, next_payment_date: datetime) -> None:
    subscription = await get_subscription(session, subscription_id, lock=True)
    if subscription is None:
        return
    subscription.retry_count = int(subscription.retry_count or 0) + 1
    subscription.next_payment_date, subscription.last_attempt_at = next_payment_date, datetime.utcnow()
    subscription.is_processing, subscription.last_payment_id = False, None
    await session.flush()


async def update_subscription_schedule(
    session: AsyncSession,
    subscription_id: int,
    *,
    is_active: bool | None = None,
    next_payment_date: datetime | None = None,
    period_days: int | None = None,
) -> YooKassaSubscription | None:
    subscription = await get_subscription(session, subscription_id, lock=True)
    if subscription is None:
        return None
    if is_active is True and not subscription.is_active:
        raise ValueError("Recurring consent must be enabled through the owned-key checkout")
    if is_active is False:
        subscription.is_active, subscription.canceled_at = False, datetime.utcnow()
    if next_payment_date is not None:
        subscription.next_payment_date = next_payment_date
    if period_days is not None:
        if period_days <= 0 or (subscription.period_days is not None and subscription.period_days != period_days):
            raise ValueError("Recurring billing period requires new explicit consent")
        subscription.period_days = period_days
    await session.flush()
    return subscription


async def set_attempt_reservation_released(session: AsyncSession, attempt_id: str) -> bool:
    """Фиксирует однократное освобождение резерва попытки оплаты."""
    attempt = await lock_attempt(session, attempt_id)
    if attempt is None:
        raise ValueError("Recurring payment attempt does not exist")
    if attempt.reservation_released:
        return False
    attempt.reservation_released = True
    await session.flush()
    return True


mark_reservation_released = set_attempt_reservation_released


async def mark_attempt_applied(
    session: AsyncSession, attempt_id: str, next_payment_date: datetime | None = None
) -> YooKassaAutopayAttempt:
    attempt = await lock_attempt(session, attempt_id)
    if attempt is None or attempt.status not in ("succeeded", "applied"):
        raise ValueError("Only a confirmed successful payment can be applied")
    if attempt.status == "applied":
        return attempt
    if attempt.subscription_id is not None and next_payment_date is not None:
        await finish_subscription_cycle(session, attempt.subscription_id, next_payment_date)
    elif attempt.subscription_id is not None:
        await session.execute(
            update(YooKassaSubscription)
            .where(YooKassaSubscription.id == attempt.subscription_id)
            .values(is_processing=False, last_payment_id=None)
        )
    attempt.status = "applied"
    await session.flush()
    return attempt


async def get_legacy_payment(session: AsyncSession, payment_id: str) -> YooKassaPayment | None:
    return await session.scalar(select(YooKassaPayment).where(YooKassaPayment.payment_id == payment_id))


async def mark_payment_status(session: AsyncSession, payment_id: str, status: str) -> YooKassaPayment | None:
    payment = await get_legacy_payment(session, payment_id)
    if payment is None:
        return None
    if payment.user_id is not None:
        await _owner(session, payment.user_id, lock=True)
    payment = await session.scalar(
        select(YooKassaPayment)
        .where(YooKassaPayment.payment_id == payment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if payment is not None and (payment.status not in ("succeeded", "canceled") or payment.status == status):
        payment.status = status
        await session.flush()
    return payment


async def get_legacy_pending(
    session: AsyncSession, subscription_id: int | None = None, user_id: int | None = None
) -> list[YooKassaPayment]:
    stmt = select(YooKassaPayment).where(
        YooKassaPayment.user_id.is_not(None), YooKassaPayment.status.in_(("pending", "waiting_for_capture"))
    )
    if subscription_id is not None:
        stmt = stmt.where(YooKassaPayment.subscription_id == subscription_id)
    if user_id is not None:
        stmt = stmt.where(YooKassaPayment.user_id == int(user_id))
    return list((await session.scalars(stmt.order_by(YooKassaPayment.created_at))).all())


async def save_payment(
    session: AsyncSession,
    user_id: int,
    *,
    payment_id: str,
    amount: float,
    status: str,
    payment_method_id: str | None = None,
    is_autopay: bool = False,
    subscription_id: int | None = None,
    metadata: dict | None = None,
    confirmation_url: str | None = None,
) -> YooKassaPayment:
    user = await _owner(session, user_id, lock=True)
    payment = await get_legacy_payment(session, payment_id)
    if payment is not None and payment.user_id != user.id:
        raise ValueError("Provider payment belongs to another or unresolved account")
    if payment is None:
        payment = YooKassaPayment(
            payment_id=payment_id, user_id=user.id, tg_id=user.tg_id, amount=amount, status=status
        )
        session.add(payment)
    elif payment.status not in ("succeeded", "canceled"):
        payment.status = status
    payment.payment_method_id, payment.is_autopay = payment_method_id, is_autopay
    payment.subscription_id, payment.confirmation_url = subscription_id, confirmation_url
    if metadata is not None:
        payment.metadata_ = json.dumps(metadata)
    await session.flush()
    return payment


async def append_log(
    session: AsyncSession, user_id: int, action: str, details: dict | None = None, level: str = "info"
) -> YooKassaLog:
    user = await _owner(session, user_id)
    entry = YooKassaLog(
        user_id=user.id,
        tg_id=user.tg_id,
        action=action,
        details=json.dumps(details) if details is not None else None,
        level=level,
    )
    session.add(entry)
    await session.flush()
    return entry


async def claim_attempt_notice(session: AsyncSession, attempt_id: str, kind: str) -> bool:
    attempt = await lock_attempt(session, attempt_id)
    if attempt is None or attempt.user_id is None:
        return False
    action = f"attempt_{kind}_{attempt.id}"
    if await session.scalar(
        select(YooKassaLog.id).where(YooKassaLog.user_id == attempt.user_id, YooKassaLog.action == action).limit(1)
    ):
        return False
    await append_log(session, attempt.user_id, action)
    return True


async def apply_verified_refund(
    session: AsyncSession,
    user_id: int,
    payment_id: str,
    refund_id: str,
    amount: float,
) -> bool:
    """Однократно применяет подтверждённый провайдером возврат."""
    return await apply_yookassa_refund(
        session,
        UserId(user_id),
        payment_id,
        refund_id,
        amount,
        provider="yookassa_autopay",
    )


async def transfer_recurring_user_data(session: AsyncSession, src_uid: int, dst_uid: int) -> None:
    """Переносит данные автоплатежей между расчётными клиентами."""
    groups = defaultdict(list)
    records = (
        await session.scalars(
            select(YooKassaSubscription)
            .where(YooKassaSubscription.user_id.in_((src_uid, dst_uid)), YooKassaSubscription.is_active.is_(True))
            .order_by(YooKassaSubscription.id)
            .with_for_update()
        )
    ).all()
    for row in records:
        groups[row.client_id].append(row)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        same_terms = (
            len({(row.card_id, row.amount, row.tariff_id, row.period_days, row.accepted_gross_amount) for row in rows})
            == 1
        )
        keep = (
            max(rows, key=lambda row: (row.created_at is not None, row.created_at, row.id)).id if same_terms else None
        )
        await session.execute(
            update(YooKassaSubscription)
            .where(YooKassaSubscription.id.in_([row.id for row in rows if row.id != keep]))
            .values(is_active=False, canceled_at=datetime.utcnow())
        )
    for model in (YooKassaSavedCard, YooKassaSubscription, YooKassaPayment, YooKassaLog, YooKassaAutopayAttempt):
        await session.execute(update(model).where(model.user_id == src_uid).values(user_id=dst_uid))


async def delete_recurring_user_data(session: AsyncSession, user_id: int) -> None:
    await session.execute(
        update(YooKassaAutopayAttempt)
        .where(YooKassaAutopayAttempt.user_id == user_id, YooKassaAutopayAttempt.status.in_(UNFINISHED_STATUSES))
        .values(status="manual_review", consent_canceled=True)
    )
    await session.execute(
        update(YooKassaAutopayAttempt).where(YooKassaAutopayAttempt.user_id == user_id).values(user_id=None)
    )
    await session.execute(delete(YooKassaSubscription).where(YooKassaSubscription.user_id == user_id))
    await session.execute(delete(YooKassaSavedCard).where(YooKassaSavedCard.user_id == user_id))
    for model in (YooKassaPayment, YooKassaLog):
        await session.execute(update(model).where(model.user_id == user_id).values(user_id=None))
