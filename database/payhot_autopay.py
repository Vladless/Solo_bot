import uuid

from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.autopay import ensure_no_other_autopay
from database.models import Key, Payment, User
from database.models.payhot_autopay import PayHotAutopayAttempt, PayHotSavedMethod, PayHotSubscription
from settings import texts


UNFINISHED_STATUSES = ("prepared", "unknown", "pending", "succeeded", "manual_review")


async def get_owner(session: AsyncSession, user_id: int, lock: bool = False):
    """Находит канонического владельца и при необходимости блокирует."""
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError(texts.PAYHOT_AUTOPAY_OWNER_ID_REQUIRED)
    query = select(User).where(User.id == user_id).execution_options(populate_existing=True)
    user = await session.scalar(query.with_for_update() if lock else query)
    if user is None:
        raise ValueError(texts.PAYHOT_AUTOPAY_OWNER_NOT_FOUND)
    return user


async def get_owned_key(session: AsyncSession, user_id: int, client_id: str, lock: bool = False):
    """Находит подписку только у указанного владельца."""
    if lock:
        await get_owner(session, user_id, lock=True)
    query = select(Key).where(Key.user_id == user_id, Key.client_id == client_id)
    return await session.scalar(query.with_for_update().execution_options(populate_existing=True) if lock else query)


async def list_methods(session: AsyncSession, user_id: int):
    """Возвращает сохранённые способы оплаты владельца."""
    return list(
        (
            await session.scalars(
                select(PayHotSavedMethod)
                .where(PayHotSavedMethod.user_id == user_id, PayHotSavedMethod.deleted_at.is_(None))
                .order_by(PayHotSavedMethod.created_at.desc(), PayHotSavedMethod.id)
            )
        ).all()
    )


async def get_owned_method(session: AsyncSession, user_id: int, method_id: str):
    """Находит действующий способ только у владельца."""
    return await session.scalar(
        select(PayHotSavedMethod).where(
            PayHotSavedMethod.user_id == user_id,
            PayHotSavedMethod.id == method_id,
            PayHotSavedMethod.is_active.is_(True),
            PayHotSavedMethod.deleted_at.is_(None),
        )
    )


async def get_active_method(session: AsyncSession, user_id: int):
    """Выбирает последнее действующее разрешение владельца."""
    return await session.scalar(
        select(PayHotSavedMethod)
        .where(
            PayHotSavedMethod.user_id == user_id,
            PayHotSavedMethod.is_active.is_(True),
            PayHotSavedMethod.deleted_at.is_(None),
        )
        .order_by(PayHotSavedMethod.created_at.desc(), PayHotSavedMethod.id)
        .limit(1)
    )


async def save_verified_method(session: AsyncSession, user_id: int, mandate: dict, source_payment_id: str):
    """Сохраняет разрешение из проверенного ответа кассы."""
    await get_owner(session, user_id, lock=True)
    method = await session.scalar(
        select(PayHotSavedMethod).where(PayHotSavedMethod.mandate_id == mandate["id"]).with_for_update()
    )
    if method is not None:
        if (
            method.user_id != user_id
            or method.payment_method != mandate["payment_method"]
            or method.merchant_mandate_reference != mandate["merchant_mandate_reference"]
        ):
            raise ValueError(texts.PAYHOT_AUTOPAY_METHOD_OWNER_MISMATCH)
        return method
    method = PayHotSavedMethod(
        id=str(uuid.uuid4()),
        user_id=user_id,
        mandate_id=mandate["id"],
        merchant_mandate_reference=mandate["merchant_mandate_reference"],
        customer_reference=f"u{user_id}",
        payment_method=mandate["payment_method"],
        source_payment_id=source_payment_id,
        display_mask=str(mandate.get("display_mask") or "")[:128],
        is_active=True,
    )
    session.add(method)
    await session.flush()
    return method


async def list_subscriptions(session: AsyncSession, user_id: int):
    """Возвращает настройки автоплатежей владельца."""
    return list(
        (
            await session.scalars(
                select(PayHotSubscription)
                .where(PayHotSubscription.user_id == user_id)
                .order_by(PayHotSubscription.id.desc())
            )
        ).all()
    )


async def get_owned_subscription(session: AsyncSession, user_id: int, client_id: str):
    """Находит активное автопродление подписки владельца."""
    return await session.scalar(
        select(PayHotSubscription)
        .where(
            PayHotSubscription.user_id == user_id,
            PayHotSubscription.client_id == client_id,
            PayHotSubscription.is_active.is_(True),
        )
        .order_by(PayHotSubscription.id.desc())
        .limit(1)
    )


async def get_subscription(session: AsyncSession, subscription_id: int, lock: bool = False):
    """Находит настройку автопродления и блокирует владельца."""
    query = select(PayHotSubscription).where(PayHotSubscription.id == subscription_id)
    if lock:
        uid = await session.scalar(select(PayHotSubscription.user_id).where(PayHotSubscription.id == subscription_id))
        if uid is not None:
            await get_owner(session, uid, lock=True)
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


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
):
    """Создаёт автопродление после проверки владельца и согласия."""
    await get_owner(session, user_id, lock=True)
    if consent.get("accepted") is not True or period_days <= 0:
        raise ValueError(texts.PAYHOT_AUTOPAY_TERMS_CONSENT_REQUIRED)
    await ensure_no_other_autopay(session, user_id, client_id, "PAYHOT_AUTOPAY")
    if (
        await get_owned_key(session, user_id, client_id) is None
        or await get_owned_method(session, user_id, method_id) is None
    ):
        raise ValueError(texts.PAYHOT_AUTOPAY_METHOD_OWNER_MISMATCH)
    existing = await session.scalar(
        select(PayHotAutopayAttempt.id)
        .join(PayHotSubscription, PayHotSubscription.id == PayHotAutopayAttempt.subscription_id)
        .where(
            PayHotSubscription.user_id == user_id,
            PayHotSubscription.client_id == client_id,
            PayHotAutopayAttempt.status.in_(UNFINISHED_STATUSES),
        )
        .limit(1)
    )
    if existing:
        raise ValueError(texts.PAYHOT_AUTOPAY_PREVIOUS_PAYMENT_PENDING)
    await session.execute(
        update(PayHotSubscription)
        .where(
            PayHotSubscription.user_id == user_id,
            PayHotSubscription.client_id == client_id,
            PayHotSubscription.is_active.is_(True),
        )
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    row = PayHotSubscription(
        user_id=user_id,
        client_id=client_id,
        method_id=method_id,
        amount=amount,
        period_days=period_days,
        tariff_id=tariff_id,
        next_payment_date=next_payment_date,
        consent={**consent, "accepted_at": datetime.utcnow().isoformat()},
        is_active=True,
        retry_count=0,
    )
    session.add(row)
    await session.flush()
    return row


async def cancel_subscription(session: AsyncSession, user_id: int, subscription_id: int) -> bool:
    """Отключает будущие отправки, сохраняя сверку уже отправленных."""
    await get_owner(session, user_id, lock=True)
    row = await get_subscription(session, subscription_id)
    if row is None or row.user_id != user_id:
        return False
    row.is_active, row.canceled_at = False, datetime.utcnow()
    payments = await session.scalars(
        select(Payment).where(
            Payment.user_id == user_id,
            Payment.payment_system == "payhot",
            or_(
                Payment.metadata_["payhot_fulfilled_client_id"].as_string() == row.client_id,
                Payment.metadata_["payhot_checkout_intent"]["data"]["client_id"].as_string() == row.client_id,
            ),
        )
    )
    for payment in payments:
        if (payment.metadata_ or {}).get("autopay_consent") is True:
            payment.metadata_ = {
                **payment.metadata_,
                "payhot_autopay_requires_consent": True,
                "payhot_enrollment_pending": False,
            }
    await session.execute(
        update(PayHotAutopayAttempt)
        .where(PayHotAutopayAttempt.user_id == user_id, PayHotAutopayAttempt.subscription_id == subscription_id)
        .values(consent_canceled=True)
    )
    await session.flush()
    return True


async def deactivate_method(session: AsyncSession, user_id: int, method_id: str) -> bool:
    """Удаляет способ и останавливает связанные автопродления."""
    await get_owner(session, user_id, lock=True)
    method = await get_owned_method(session, user_id, method_id)
    if method is None:
        return False
    method.is_active, method.deleted_at = False, datetime.utcnow()
    await session.execute(
        update(PayHotSubscription)
        .where(PayHotSubscription.user_id == user_id, PayHotSubscription.method_id == method_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.execute(
        update(PayHotAutopayAttempt)
        .where(PayHotAutopayAttempt.user_id == user_id, PayHotAutopayAttempt.method_id == method_id)
        .values(consent_canceled=True)
    )
    await session.flush()
    return True


async def list_due_subscriptions(session: AsyncSession):
    """Выбирает активные подписки с наступившим сроком оплаты."""
    return list(
        (
            await session.scalars(
                select(PayHotSubscription)
                .where(
                    PayHotSubscription.user_id.is_not(None),
                    PayHotSubscription.is_active.is_(True),
                    PayHotSubscription.next_payment_date <= datetime.utcnow(),
                )
                .order_by(PayHotSubscription.next_payment_date)
            )
        ).all()
    )


async def get_attempt_by_payment_id(session: AsyncSession, payment_id: str):
    """Находит попытку по собственному номеру платежа."""
    return await session.scalar(select(PayHotAutopayAttempt).where(PayHotAutopayAttempt.payment_id == payment_id))


async def lock_attempt(session: AsyncSession, attempt_id: str):
    """Блокирует владельца перед изменением попытки."""
    uid = await session.scalar(select(PayHotAutopayAttempt.user_id).where(PayHotAutopayAttempt.id == attempt_id))
    if uid is not None:
        await get_owner(session, uid, lock=True)
    return await session.scalar(
        select(PayHotAutopayAttempt)
        .where(PayHotAutopayAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def get_open_subscription_attempt(session: AsyncSession, subscription_id: int):
    """Исключает параллельное списание по одной подписке."""
    return await session.scalar(
        select(PayHotAutopayAttempt)
        .where(
            PayHotAutopayAttempt.subscription_id == subscription_id,
            PayHotAutopayAttempt.status.in_(UNFINISHED_STATUSES),
        )
        .limit(1)
    )


async def list_unfinished_attempts(session: AsyncSession, user_id: int | None = None):
    """Возвращает попытки для обязательной фоновой сверки."""
    query = select(PayHotAutopayAttempt).where(PayHotAutopayAttempt.status.in_(UNFINISHED_STATUSES))
    if user_id is not None:
        query = query.where(PayHotAutopayAttempt.user_id == user_id)
    return list((await session.scalars(query.order_by(PayHotAutopayAttempt.created_at))).all())


async def transfer_recurring_user_data(session: AsyncSession, src_uid: int, dst_uid: int) -> None:
    """Переносит данные, отключая прежнее согласие обоих владельцев."""
    if src_uid == dst_uid:
        return
    for uid in sorted((src_uid, dst_uid)):
        await get_owner(session, uid, lock=True)
    await session.execute(
        update(PayHotSubscription)
        .where(PayHotSubscription.user_id.in_((src_uid, dst_uid)))
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.execute(
        update(PayHotAutopayAttempt)
        .where(PayHotAutopayAttempt.user_id.in_((src_uid, dst_uid)))
        .values(consent_canceled=True)
    )
    payments = await session.scalars(
        select(Payment).where(Payment.user_id.in_((src_uid, dst_uid)), Payment.payment_system == "payhot")
    )
    for payment in payments:
        if (payment.metadata_ or {}).get("autopay_consent") is True:
            payment.metadata_ = {
                **payment.metadata_,
                "payhot_autopay_requires_consent": True,
                "payhot_enrollment_pending": False,
            }
    for model in (PayHotSavedMethod, PayHotSubscription, PayHotAutopayAttempt):
        await session.execute(update(model).where(model.user_id == src_uid).values(user_id=dst_uid))
    await session.flush()


async def delete_recurring_user_data(session: AsyncSession, user_id: int) -> None:
    """Останавливает отправки удаляемого владельца без потери сверки."""
    await get_owner(session, user_id, lock=True)
    await session.execute(
        update(PayHotSavedMethod)
        .where(PayHotSavedMethod.user_id == user_id)
        .values(is_active=False, deleted_at=datetime.utcnow())
    )
    await session.execute(
        update(PayHotSubscription)
        .where(PayHotSubscription.user_id == user_id)
        .values(is_active=False, canceled_at=datetime.utcnow())
    )
    await session.execute(
        update(PayHotAutopayAttempt)
        .where(PayHotAutopayAttempt.user_id == user_id)
        .values(consent_canceled=True, owner_deleted=True)
    )
    await session.flush()
