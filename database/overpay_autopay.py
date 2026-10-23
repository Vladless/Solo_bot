import uuid

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.autopay import ensure_no_other_autopay
from database.models import (
    Key,
    OverPayAutopayAttempt,
    OverPayAutopayLog,
    OverPaySavedMethod,
    OverPaySubscription,
    User,
)
from settings import texts


UNFINISHED_STATUSES = ("prepared", "unknown", "pending", "succeeded", "manual_review")


async def get_owner(session: AsyncSession, user_id: int, lock: bool = False) -> User:
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError(texts.OVERPAY_AUTOPAY_OWNER_ID_REQUIRED)
    statement = select(User).where(User.id == user_id).execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update()
    user = await session.scalar(statement)
    if user is None:
        raise ValueError(texts.OVERPAY_AUTOPAY_OWNER_NOT_FOUND)
    return user


async def get_owned_key(session: AsyncSession, user_id: int, client_id: str, lock: bool = False) -> Key | None:
    if lock:
        await get_owner(session, user_id, lock=True)
    statement = select(Key).where(Key.user_id == user_id, Key.client_id == client_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(statement)


async def list_methods(session: AsyncSession, user_id: int) -> list[OverPaySavedMethod]:
    return list(
        (
            await session.scalars(
                select(OverPaySavedMethod)
                .where(OverPaySavedMethod.user_id == user_id, OverPaySavedMethod.deleted_at.is_(None))
                .order_by(OverPaySavedMethod.created_at.desc(), OverPaySavedMethod.id)
            )
        ).all()
    )


async def get_owned_method(session: AsyncSession, user_id: int, method_id: str) -> OverPaySavedMethod | None:
    return await session.scalar(
        select(OverPaySavedMethod).where(
            OverPaySavedMethod.user_id == user_id,
            OverPaySavedMethod.id == method_id,
            OverPaySavedMethod.deleted_at.is_(None),
        )
    )


async def get_active_method(session: AsyncSession, user_id: int) -> OverPaySavedMethod | None:
    return await session.scalar(
        select(OverPaySavedMethod)
        .where(
            OverPaySavedMethod.user_id == user_id,
            OverPaySavedMethod.is_active.is_(True),
            OverPaySavedMethod.deleted_at.is_(None),
        )
        .order_by(OverPaySavedMethod.created_at.desc(), OverPaySavedMethod.id)
        .limit(1)
    )


async def save_verified_method(session: AsyncSession, user_id: int, order_id: str) -> OverPaySavedMethod:
    """Сохраняет исходный оплаченный заказ владельца для повторных списаний."""
    await get_owner(session, user_id, lock=True)
    if not isinstance(order_id, str) or not order_id.isascii() or not order_id.isdecimal() or len(order_id) > 64:
        raise ValueError(texts.OVERPAY_AUTOPAY_METHOD_INVALID)
    method = await session.scalar(
        select(OverPaySavedMethod).where(OverPaySavedMethod.source_payment_id == order_id).with_for_update()
    )
    if method is not None:
        if method.user_id != user_id:
            raise ValueError(texts.OVERPAY_AUTOPAY_METHOD_OWNER_MISMATCH)
        return method
    method = OverPaySavedMethod(id=str(uuid.uuid4()), user_id=user_id, source_payment_id=order_id, is_active=True)
    session.add(method)
    await session.flush()
    return method


async def list_subscriptions(session: AsyncSession, user_id: int) -> list[OverPaySubscription]:
    return list(
        (
            await session.scalars(
                select(OverPaySubscription)
                .where(OverPaySubscription.user_id == user_id)
                .order_by(OverPaySubscription.id.desc())
            )
        ).all()
    )


async def get_owned_subscription(session: AsyncSession, user_id: int, client_id: str) -> OverPaySubscription | None:
    return await session.scalar(
        select(OverPaySubscription)
        .where(
            OverPaySubscription.user_id == user_id,
            OverPaySubscription.client_id == client_id,
            OverPaySubscription.is_active.is_(True),
        )
        .order_by(OverPaySubscription.id.desc())
        .limit(1)
    )


async def get_subscription(session: AsyncSession, subscription_id: int, lock: bool = False):
    statement = select(OverPaySubscription).where(OverPaySubscription.id == subscription_id)
    if lock:
        owner_id = await session.scalar(
            select(OverPaySubscription.user_id).where(OverPaySubscription.id == subscription_id)
        )
        if owner_id is not None:
            await get_owner(session, owner_id, lock=True)
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(statement)


async def upsert_subscription(
    session: AsyncSession,
    user_id: int,
    client_id: str,
    method_id: str,
    *,
    amount,
    period_days: int,
    tariff_id: int,
    next_payment_date: datetime,
    consent: dict,
) -> OverPaySubscription:
    await get_owner(session, user_id, lock=True)
    if consent.get("accepted") is not True or period_days <= 0:
        raise ValueError(texts.OVERPAY_AUTOPAY_TERMS_CONSENT_REQUIRED)
    await ensure_no_other_autopay(session, user_id, client_id, "OVERPAY_AUTOPAY")
    key = await get_owned_key(session, user_id, client_id)
    method = await get_owned_method(session, user_id, method_id)
    if key is None or method is None:
        raise ValueError(texts.OVERPAY_AUTOPAY_METHOD_OWNER_MISMATCH)
    unfinished = await session.scalar(
        select(OverPayAutopayAttempt.id)
        .join(OverPaySubscription, OverPaySubscription.id == OverPayAutopayAttempt.subscription_id)
        .where(
            OverPaySubscription.user_id == user_id,
            OverPaySubscription.client_id == client_id,
            OverPayAutopayAttempt.status.in_(UNFINISHED_STATUSES),
        )
        .limit(1)
    )
    if unfinished:
        raise ValueError(texts.OVERPAY_AUTOPAY_PREVIOUS_PAYMENT_PENDING)
    await session.execute(
        update(OverPaySubscription)
        .where(
            OverPaySubscription.user_id == user_id,
            OverPaySubscription.client_id == client_id,
            OverPaySubscription.is_active.is_(True),
        )
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    subscription = OverPaySubscription(
        user_id=user_id,
        client_id=client_id,
        method_id=method_id,
        tariff_id=tariff_id,
        amount=amount,
        period_days=period_days,
        next_payment_date=next_payment_date,
        consent={**consent, "accepted_at": datetime.utcnow().isoformat()},
        is_active=True,
        retry_count=0,
    )
    session.add(subscription)
    await session.flush()
    return subscription


async def cancel_subscription(session: AsyncSession, user_id: int, subscription_id: int) -> bool:
    await get_owner(session, user_id, lock=True)
    subscription = await get_subscription(session, subscription_id)
    if subscription is None or subscription.user_id != user_id:
        return False
    await session.execute(
        update(OverPayAutopayAttempt)
        .where(OverPayAutopayAttempt.user_id == user_id, OverPayAutopayAttempt.subscription_id == subscription_id)
        .values(consent_canceled=True)
    )
    attempts = list(
        (
            await session.scalars(
                select(OverPayAutopayAttempt).where(
                    OverPayAutopayAttempt.user_id == user_id, OverPayAutopayAttempt.subscription_id.is_(None)
                )
            )
        ).all()
    )
    for attempt in attempts:
        if (
            attempt.fulfilled_client_id == subscription.client_id
            or (attempt.intent.get("metadata") or {}).get("autopay_client_id") == subscription.client_id
        ):
            attempt.consent_canceled = True
    result = await session.execute(
        update(OverPaySubscription)
        .where(OverPaySubscription.user_id == user_id, OverPaySubscription.id == subscription_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.flush()
    return bool(result.rowcount)


async def deactivate_method(session: AsyncSession, user_id: int, method_id: str) -> bool:
    await get_owner(session, user_id, lock=True)
    method = await get_owned_method(session, user_id, method_id)
    if method is None:
        return False
    method.is_active, method.deleted_at = False, datetime.utcnow()
    await session.execute(
        update(OverPayAutopayAttempt)
        .where(OverPayAutopayAttempt.user_id == user_id, OverPayAutopayAttempt.method_id == method_id)
        .values(consent_canceled=True)
    )
    await session.execute(
        update(OverPaySubscription)
        .where(OverPaySubscription.user_id == user_id, OverPaySubscription.method_id == method_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.flush()
    return True


async def list_due_subscriptions(session: AsyncSession) -> list[OverPaySubscription]:
    return list(
        (
            await session.scalars(
                select(OverPaySubscription)
                .where(
                    OverPaySubscription.user_id.is_not(None),
                    OverPaySubscription.is_active.is_(True),
                    OverPaySubscription.next_payment_date <= datetime.utcnow(),
                )
                .order_by(OverPaySubscription.next_payment_date)
            )
        ).all()
    )


async def get_attempt(session: AsyncSession, attempt_id: str):
    return await session.get(OverPayAutopayAttempt, attempt_id)


async def lock_attempt(session: AsyncSession, attempt_id: str):
    owner_id = await session.scalar(select(OverPayAutopayAttempt.user_id).where(OverPayAutopayAttempt.id == attempt_id))
    if owner_id is not None:
        await get_owner(session, owner_id, lock=True)
    return await session.scalar(
        select(OverPayAutopayAttempt)
        .where(OverPayAutopayAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def get_attempt_by_payment_id(session: AsyncSession, payment_id: str):
    return await session.scalar(select(OverPayAutopayAttempt).where(OverPayAutopayAttempt.payment_id == payment_id))


async def list_unfinished_attempts(session: AsyncSession, user_id: int | None = None):
    statement = select(OverPayAutopayAttempt).where(OverPayAutopayAttempt.status.in_(UNFINISHED_STATUSES))
    if user_id is not None:
        statement = statement.where(OverPayAutopayAttempt.user_id == user_id)
    return list((await session.scalars(statement.order_by(OverPayAutopayAttempt.created_at))).all())


async def list_unfinalized_cancellations(session: AsyncSession):
    return list(
        (
            await session.scalars(
                select(OverPayAutopayAttempt).where(
                    OverPayAutopayAttempt.status == "canceled",
                    OverPayAutopayAttempt.payment_finalized.is_(False),
                    OverPayAutopayAttempt.owner_deleted.is_(False),
                    OverPayAutopayAttempt.user_id.is_not(None),
                )
            )
        ).all()
    )


async def get_open_subscription_attempt(session: AsyncSession, subscription_id: int):
    return await session.scalar(
        select(OverPayAutopayAttempt)
        .where(
            OverPayAutopayAttempt.subscription_id == subscription_id,
            OverPayAutopayAttempt.status.in_(UNFINISHED_STATUSES),
        )
        .limit(1)
    )


async def find_open_checkout_attempt(session: AsyncSession, user_id: int, fingerprint: str):
    attempts = await list_unfinished_attempts(session, user_id)
    return next(
        (
            row
            for row in attempts
            if row.subscription_id is None and (row.intent or {}).get("checkout_fingerprint") == fingerprint
        ),
        None,
    )


async def prepare_attempt(
    session: AsyncSession,
    user_id: int,
    *,
    attempt_id: str,
    payment_id: str,
    payload: dict,
    intent: dict,
    subscription_id: int | None = None,
    method_id: str | None = None,
):
    await get_owner(session, user_id, lock=True)
    if subscription_id is not None:
        subscription = await get_subscription(session, subscription_id, lock=True)
        if subscription is None or subscription.user_id != user_id or not subscription.is_active:
            raise ValueError(texts.OVERPAY_AUTOPAY_SUBSCRIPTION_UNAVAILABLE)
        existing = await get_open_subscription_attempt(session, subscription_id)
        if existing:
            return existing
    attempt = OverPayAutopayAttempt(
        id=attempt_id,
        user_id=user_id,
        subscription_id=subscription_id,
        method_id=method_id,
        payment_id=payment_id,
        payload=payload,
        intent=intent,
        status="prepared",
    )
    session.add(attempt)
    await session.flush()
    session.add(OverPayAutopayLog(attempt_id=attempt.id, status="prepared"))
    return attempt


async def transfer_recurring_user_data(session: AsyncSession, src_uid: int, dst_uid: int) -> None:
    if src_uid == dst_uid:
        return
    for uid in sorted((src_uid, dst_uid)):
        await get_owner(session, uid, lock=True)
    await session.execute(
        update(OverPaySubscription)
        .where(OverPaySubscription.user_id.in_((src_uid, dst_uid)))
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.execute(
        update(OverPayAutopayAttempt)
        .where(OverPayAutopayAttempt.user_id.in_((src_uid, dst_uid)))
        .values(consent_canceled=True)
    )
    for model in (OverPaySavedMethod, OverPaySubscription, OverPayAutopayAttempt):
        await session.execute(update(model).where(model.user_id == src_uid).values(user_id=dst_uid))
    await session.flush()


async def delete_recurring_user_data(session: AsyncSession, user_id: int) -> None:
    await get_owner(session, user_id, lock=True)
    await session.execute(
        update(OverPaySavedMethod)
        .where(OverPaySavedMethod.user_id == user_id)
        .values(is_active=False, deleted_at=datetime.utcnow())
    )
    await session.execute(
        update(OverPaySubscription)
        .where(OverPaySubscription.user_id == user_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.execute(
        update(OverPayAutopayAttempt)
        .where(OverPayAutopayAttempt.user_id == user_id)
        .values(consent_canceled=True, owner_deleted=True)
    )
    await session.flush()


async def get_attempt_by_provider_order_id(session: AsyncSession, order_id: str):
    return await session.scalar(
        select(OverPayAutopayAttempt).where(OverPayAutopayAttempt.provider_order_id == order_id)
    )
