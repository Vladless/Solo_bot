import hashlib
import uuid

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    KassaIAutopayAttempt,
    KassaISavedMethod,
    KassaISubscription,
    Key,
    User,
    YooKassaAutopayAttempt,
    YooKassaSubscription,
)


UNFINISHED_STATUSES = ("prepared", "unknown", "pending", "succeeded", "manual_review")


async def get_owner(session: AsyncSession, user_id: int, lock: bool = False) -> User:
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError("Требуется внутренний идентификатор клиента")
    statement = select(User).where(User.id == user_id).execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update()
    user = await session.scalar(statement)
    if user is None:
        raise ValueError("Клиент оплаты не найден")
    return user


async def get_owned_key(session: AsyncSession, user_id: int, client_id: str, lock: bool = False) -> Key | None:
    if lock:
        await get_owner(session, user_id, lock=True)
    statement = select(Key).where(Key.user_id == user_id, Key.client_id == client_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(statement)


async def list_methods(session: AsyncSession, user_id: int) -> list[KassaISavedMethod]:
    return list(
        (
            await session.scalars(
                select(KassaISavedMethod)
                .where(KassaISavedMethod.user_id == user_id, KassaISavedMethod.deleted_at.is_(None))
                .order_by(KassaISavedMethod.created_at.desc(), KassaISavedMethod.id)
            )
        ).all()
    )


async def get_owned_method(session: AsyncSession, user_id: int, method_id: str) -> KassaISavedMethod | None:
    return await session.scalar(
        select(KassaISavedMethod).where(
            KassaISavedMethod.user_id == user_id,
            KassaISavedMethod.id == method_id,
            KassaISavedMethod.deleted_at.is_(None),
        )
    )


async def get_active_method(session: AsyncSession, user_id: int) -> KassaISavedMethod | None:
    return await session.scalar(
        select(KassaISavedMethod)
        .where(
            KassaISavedMethod.user_id == user_id,
            KassaISavedMethod.is_active.is_(True),
            KassaISavedMethod.deleted_at.is_(None),
        )
        .order_by(KassaISavedMethod.created_at.desc(), KassaISavedMethod.id)
        .limit(1)
    )


async def save_verified_method(session: AsyncSession, user_id: int, token: str, payment_id: str) -> KassaISavedMethod:
    await get_owner(session, user_id, lock=True)
    if not isinstance(token, str) or not token or len(token) > 4096:
        raise ValueError("Некорректная привязка СБП")
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    method = await session.scalar(
        select(KassaISavedMethod).where(KassaISavedMethod.token_hash == token_hash).with_for_update()
    )
    if method is not None:
        if method.user_id != user_id:
            raise ValueError("Привязка принадлежит другому клиенту")
        return method
    previous = await session.scalar(
        select(KassaISavedMethod.id).where(KassaISavedMethod.source_payment_id == payment_id)
    )
    if previous is not None:
        raise ValueError("Заказ уже связан с другой привязкой СБП")
    method = KassaISavedMethod(
        id=str(uuid.uuid4()),
        user_id=user_id,
        token=token,
        token_hash=token_hash,
        source_payment_id=payment_id,
        is_active=True,
    )
    session.add(method)
    await session.flush()
    return method


async def list_subscriptions(session: AsyncSession, user_id: int) -> list[KassaISubscription]:
    return list(
        (
            await session.scalars(
                select(KassaISubscription)
                .where(KassaISubscription.user_id == user_id)
                .order_by(KassaISubscription.id.desc())
            )
        ).all()
    )


async def get_owned_subscription(session: AsyncSession, user_id: int, client_id: str) -> KassaISubscription | None:
    return await session.scalar(
        select(KassaISubscription)
        .where(
            KassaISubscription.user_id == user_id,
            KassaISubscription.client_id == client_id,
            KassaISubscription.is_active.is_(True),
        )
        .order_by(KassaISubscription.id.desc())
        .limit(1)
    )


async def get_subscription(session: AsyncSession, subscription_id: int, lock: bool = False):
    statement = select(KassaISubscription).where(KassaISubscription.id == subscription_id)
    if lock:
        owner_id = await session.scalar(
            select(KassaISubscription.user_id).where(KassaISubscription.id == subscription_id)
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
) -> KassaISubscription:
    await get_owner(session, user_id, lock=True)
    if consent.get("accepted") is not True or period_days <= 0:
        raise ValueError("Требуется согласие на условия автоплатежа")
    if await session.scalar(
        select(YooKassaSubscription.id)
        .where(
            YooKassaSubscription.user_id == user_id,
            YooKassaSubscription.client_id == client_id,
            YooKassaSubscription.is_active.is_(True),
        )
        .limit(1)
    ):
        raise ValueError("Сначала отключите автоплатёж ЮКассы для этой подписки")
    if await session.scalar(
        select(YooKassaAutopayAttempt.id)
        .join(YooKassaSubscription, YooKassaSubscription.id == YooKassaAutopayAttempt.subscription_id)
        .where(
            YooKassaSubscription.user_id == user_id,
            YooKassaSubscription.client_id == client_id,
            YooKassaAutopayAttempt.status.in_(UNFINISHED_STATUSES),
        )
        .limit(1)
    ):
        raise ValueError("Сначала дождитесь результата предыдущего автоплатежа ЮКассы")
    key = await get_owned_key(session, user_id, client_id)
    method = await get_owned_method(session, user_id, method_id)
    if key is None or method is None:
        raise ValueError("Ключ и привязка должны принадлежать клиенту")
    unfinished = await session.scalar(
        select(KassaIAutopayAttempt.id)
        .join(KassaISubscription, KassaISubscription.id == KassaIAutopayAttempt.subscription_id)
        .where(
            KassaISubscription.user_id == user_id,
            KassaISubscription.client_id == client_id,
            KassaIAutopayAttempt.status.in_(UNFINISHED_STATUSES),
        )
        .limit(1)
    )
    if unfinished:
        raise ValueError("Предыдущий автоплатёж ещё не завершён")
    await session.execute(
        update(KassaISubscription)
        .where(
            KassaISubscription.user_id == user_id,
            KassaISubscription.client_id == client_id,
            KassaISubscription.is_active.is_(True),
        )
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    subscription = KassaISubscription(
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
        update(KassaIAutopayAttempt)
        .where(KassaIAutopayAttempt.user_id == user_id, KassaIAutopayAttempt.subscription_id == subscription_id)
        .values(consent_canceled=True)
    )
    attempts = list(
        (
            await session.scalars(
                select(KassaIAutopayAttempt).where(
                    KassaIAutopayAttempt.user_id == user_id, KassaIAutopayAttempt.subscription_id.is_(None)
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
        update(KassaISubscription)
        .where(KassaISubscription.user_id == user_id, KassaISubscription.id == subscription_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.flush()
    return bool(result.rowcount)


async def deactivate_method(session: AsyncSession, user_id: int, method_id: str) -> bool:
    await get_owner(session, user_id, lock=True)
    method = await get_owned_method(session, user_id, method_id)
    if method is None:
        return False
    method.is_active, method.deleted_at, method.revoke_pending = False, datetime.utcnow(), True
    await session.execute(
        update(KassaIAutopayAttempt)
        .where(KassaIAutopayAttempt.user_id == user_id, KassaIAutopayAttempt.method_id == method_id)
        .values(consent_canceled=True)
    )
    await session.execute(
        update(KassaISubscription)
        .where(KassaISubscription.user_id == user_id, KassaISubscription.method_id == method_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.flush()
    return True


async def list_due_subscriptions(session: AsyncSession) -> list[KassaISubscription]:
    return list(
        (
            await session.scalars(
                select(KassaISubscription)
                .where(
                    KassaISubscription.user_id.is_not(None),
                    KassaISubscription.is_active.is_(True),
                    KassaISubscription.next_payment_date <= datetime.utcnow(),
                )
                .order_by(KassaISubscription.next_payment_date)
            )
        ).all()
    )


async def get_attempt(session: AsyncSession, attempt_id: str):
    return await session.get(KassaIAutopayAttempt, attempt_id)


async def lock_attempt(session: AsyncSession, attempt_id: str):
    owner_id = await session.scalar(select(KassaIAutopayAttempt.user_id).where(KassaIAutopayAttempt.id == attempt_id))
    if owner_id is not None:
        await get_owner(session, owner_id, lock=True)
    return await session.scalar(
        select(KassaIAutopayAttempt)
        .where(KassaIAutopayAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def get_attempt_by_payment_id(session: AsyncSession, payment_id: str):
    return await session.scalar(select(KassaIAutopayAttempt).where(KassaIAutopayAttempt.payment_id == payment_id))


async def list_unfinished_attempts(session: AsyncSession, user_id: int | None = None):
    statement = select(KassaIAutopayAttempt).where(KassaIAutopayAttempt.status.in_(UNFINISHED_STATUSES))
    if user_id is not None:
        statement = statement.where(KassaIAutopayAttempt.user_id == user_id)
    return list((await session.scalars(statement.order_by(KassaIAutopayAttempt.created_at))).all())


async def list_unfinalized_cancellations(session: AsyncSession):
    return list(
        (
            await session.scalars(
                select(KassaIAutopayAttempt).where(
                    KassaIAutopayAttempt.status == "canceled",
                    KassaIAutopayAttempt.payment_finalized.is_(False),
                    KassaIAutopayAttempt.owner_deleted.is_(False),
                    KassaIAutopayAttempt.user_id.is_not(None),
                )
            )
        ).all()
    )


async def get_open_subscription_attempt(session: AsyncSession, subscription_id: int):
    return await session.scalar(
        select(KassaIAutopayAttempt)
        .where(
            KassaIAutopayAttempt.subscription_id == subscription_id,
            KassaIAutopayAttempt.status.in_(UNFINISHED_STATUSES),
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
            raise ValueError("Автопродление недоступно")
        existing = await get_open_subscription_attempt(session, subscription_id)
        if existing:
            return existing
    attempt = KassaIAutopayAttempt(
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
    return attempt


async def transfer_recurring_user_data(session: AsyncSession, src_uid: int, dst_uid: int) -> None:
    if src_uid == dst_uid:
        return
    for uid in sorted((src_uid, dst_uid)):
        await get_owner(session, uid, lock=True)
    await session.execute(
        update(KassaISubscription)
        .where(KassaISubscription.user_id.in_((src_uid, dst_uid)))
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.execute(
        update(KassaIAutopayAttempt)
        .where(KassaIAutopayAttempt.user_id.in_((src_uid, dst_uid)))
        .values(consent_canceled=True)
    )
    for model in (KassaISavedMethod, KassaISubscription, KassaIAutopayAttempt):
        await session.execute(update(model).where(model.user_id == src_uid).values(user_id=dst_uid))
    await session.flush()


async def delete_recurring_user_data(session: AsyncSession, user_id: int) -> None:
    await get_owner(session, user_id, lock=True)
    await session.execute(
        update(KassaISavedMethod)
        .where(KassaISavedMethod.user_id == user_id)
        .values(is_active=False, deleted_at=datetime.utcnow(), revoke_pending=True)
    )
    await session.execute(
        update(KassaISubscription)
        .where(KassaISubscription.user_id == user_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.execute(
        update(KassaIAutopayAttempt)
        .where(KassaIAutopayAttempt.user_id == user_id)
        .values(consent_canceled=True, owner_deleted=True)
    )
    await session.flush()
