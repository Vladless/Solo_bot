from datetime import datetime

from sqlalchemy import and_, delete, func, not_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.executor import spawn
from core.redis_cache import cache_delete, cache_get, cache_key, cache_set
from database.access.resolution import (
    TelegramId,
    UserId,
    resolve_uid_cached,
    resolve_user_optional,
    user_ref_cache_key,
    user_ref_cache_keys,
)
from database.cache_purge import defer_purge, is_purge_pending
from database.models import (
    BlockedUser,
    CouponUsage,
    Gift,
    GiftUsage,
    Identity,
    Key,
    ManualBan,
    Notification,
    Payment,
    Referral,
    ScheduledBroadcast,
    TemporaryData,
    User,
    WebNotification,
    WebPushSubscription,
)
from database.partners import delete_partner_user_data
from database.yookassa_autopay import delete_recurring_user_data
from logger import logger
from settings.cache_config import (
    BALANCE_CACHE_TTL_SEC,
    PREFERRED_CURRENCY_CACHE_TTL_SEC,
    USER_EXISTS_CACHE_TTL_SEC,
    USER_SNAPSHOT_CACHE_TTL_SEC,
)


def invalidate_user_snapshot(tg_id: int) -> None:
    import asyncio

    try:
        asyncio.get_running_loop()
        spawn(cache_delete(cache_key("user_snapshot", tg_id)))
        spawn(cache_delete(user_ref_cache_key("user_snapshot", UserId(tg_id))))
    except RuntimeError:
        return


def exclude_shadow_placeholders():
    """Возвращает фильтр без теневых заглушек клиентов."""
    return not_(
        and_(
            User.first_name.is_(None),
            select(ManualBan.user_id).where(ManualBan.user_id == User.id, ManualBan.reason == "shadow").exists(),
        )
    )


async def add_user(
    session: AsyncSession,
    tg_id: int,
    username: str = None,
    first_name: str = None,
    last_name: str = None,
    language_code: str = None,
    is_bot: bool = False,
    source_code: str = None,
) -> int | None:
    stmt = (
        insert(User)
        .values(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            is_bot=is_bot,
            source_code=source_code,
        )
        .on_conflict_do_nothing(index_elements=["tg_id"])
        .returning(User.id)
    )
    res = await session.execute(stmt)
    inserted_id = res.scalar_one_or_none()
    if inserted_id is None:
        return None
    from database.access.resolution import invalidate_uid_cache

    await invalidate_uid_cache(tg_id, inserted_id)
    await cache_set(user_ref_cache_key("user_exists", tg_id), True, USER_EXISTS_CACHE_TTL_SEC)
    logger.info(f"[DB] Новый пользователь добавлен: tg_id={tg_id} id={inserted_id} (source: {source_code})")
    try:
        from services.admin_notify import notify_new_client

        await notify_new_client(session, int(inserted_id))
    except Exception as exc:
        logger.warning("[DB] Уведомление админам о новом клиенте не ушло: {}", exc)
    return int(inserted_id)


async def invalidate_balance_cache(tg_id: int) -> None:
    await cache_delete(cache_key("balance", tg_id))
    await cache_delete(user_ref_cache_key("balance", UserId(tg_id)))


async def invalidate_profile_cache(tg_id: int) -> None:
    await cache_delete(cache_key("profile_data", tg_id))
    await cache_delete(user_ref_cache_key("profile_data", UserId(tg_id)))


async def update_balance(
    session: AsyncSession,
    legacy_user_ref: int,
    amount: float,
    *,
    allow_negative: bool = False,
) -> float | None:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        logger.info(f"[DB] Баланс не изменён: пользователь {legacy_user_ref} не найден")
        return None
    uid = u.id
    amount = float(amount)
    stmt = update(User).values(balance=func.coalesce(User.balance, 0) + amount).returning(User.balance)
    if amount < 0 and not allow_negative:
        stmt = stmt.where(User.id == uid, func.coalesce(User.balance, 0) >= -amount)
    else:
        stmt = stmt.where(User.id == uid)
    res = await session.execute(stmt)
    new_balance = res.scalar_one_or_none()
    if new_balance is None:
        if amount < 0:
            logger.info(f"[DB] Списание {-amount} у id={uid} отклонено: недостаточно средств")
        else:
            logger.info(f"[DB] Баланс пользователя id={uid} не изменён: пользователь не найден")
        return None
    logger.info(f"[DB] Баланс пользователя id={uid} обновлён: {new_balance - amount} → {new_balance}")
    refs = {uid, u.tg_id} - {None}
    defer_purge(
        session,
        *(
            key
            for prefix in ("balance", "profile_data")
            for ref in refs
            for key in (cache_key(prefix, ref), user_ref_cache_key(prefix, UserId(ref)))
        ),
    )
    await invalidate_balance_cache(uid)
    await invalidate_profile_cache(uid)
    if u.tg_id is not None:
        await invalidate_balance_cache(u.tg_id)
        await invalidate_profile_cache(u.tg_id)
    return float(new_balance)


async def check_user_exists(session: AsyncSession, legacy_user_ref: int) -> bool:
    ckey = user_ref_cache_key("user_exists", legacy_user_ref)
    pending = is_purge_pending(session, ckey)
    cached = None if pending else await cache_get(ckey)
    if isinstance(cached, bool):
        return cached
    u = await resolve_user_optional(session, legacy_user_ref)
    value = u is not None
    if not pending:
        await cache_set(ckey, value, USER_EXISTS_CACHE_TTL_SEC)
    return value


async def get_balance(session: AsyncSession, legacy_user_ref: int) -> float:
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return 0.0
    ckey = user_ref_cache_key("balance", UserId(uid))
    pending = is_purge_pending(session, ckey)
    cached = None if pending else await cache_get(ckey)
    if cached is not None:
        try:
            return round(float(cached), 1)
        except (TypeError, ValueError):
            pass
    result = await session.execute(select(func.coalesce(User.balance, 0.0)).where(User.id == uid))
    balance = result.scalar_one_or_none()
    value = round(float(balance or 0.0), 1)
    if not pending:
        await cache_set(ckey, value, BALANCE_CACHE_TTL_SEC)
    return value


async def get_locked_balance(session: AsyncSession, user_id: UserId) -> float | None:
    """Читает актуальный баланс под блокировкой клиента."""
    if not isinstance(user_id, UserId):
        raise TypeError("Expected UserId")
    value = await session.scalar(
        select(func.coalesce(User.balance, 0.0)).where(User.id == int(user_id)).with_for_update()
    )
    return float(value) if value is not None else None


async def set_user_balance(
    session: AsyncSession,
    legacy_user_ref: int,
    balance: float,
) -> None:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    uid = u.id
    balance = float(balance)
    await session.execute(update(User).where(User.id == uid).values(balance=balance))
    defer_purge(
        session,
        *(
            key
            for prefix in ("balance", "profile_data")
            for ref in {uid, u.tg_id} - {None}
            for key in (cache_key(prefix, ref), user_ref_cache_key(prefix, UserId(ref)))
        ),
    )
    await invalidate_balance_cache(uid)
    await invalidate_profile_cache(uid)


async def get_user_preferred_currency(session: AsyncSession, tg_id: int) -> str | None:
    """Возвращает предпочитаемую валюту клиента."""
    ckey = user_ref_cache_key("pref_ccy", tg_id)
    pending = is_purge_pending(session, ckey)
    cached = None if pending else await cache_get(ckey)
    if isinstance(cached, str):
        return cached or None
    field = User.id if isinstance(tg_id, UserId) else User.tg_id
    result = await session.execute(select(User.preferred_currency).where(field == int(tg_id)))
    value = result.scalar()
    if not pending:
        await cache_set(ckey, value or "", PREFERRED_CURRENCY_CACHE_TTL_SEC)
    return value


async def mark_trial_started_if_eligible(session: AsyncSession, tg_id: int) -> None:
    """Отмечает начало ещё не использованного пробного периода."""
    field = User.id if isinstance(tg_id, UserId) else User.tg_id
    await session.execute(update(User).where(field == tg_id, User.trial.in_([0, -1])).values(trial=1))


async def update_trial(session: AsyncSession, legacy_user_ref: int, status: int):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    uid = u.id
    await session.execute(update(User).where(User.id == uid).values(trial=status))
    refs = [uid] if u.tg_id is None else [uid, u.tg_id]
    keys = [
        key
        for ref in refs
        for name in ("profile_data", "user_snapshot")
        for key in (cache_key(name, ref), user_ref_cache_key(name, UserId(ref)))
    ]
    if not defer_purge(session, *keys):
        await invalidate_profile_cache(uid)
        invalidate_user_snapshot(uid)
        if u.tg_id is not None:
            await invalidate_profile_cache(u.tg_id)
            invalidate_user_snapshot(u.tg_id)
    logger.info(f"[DB] Триал статус обновлён для пользователя id={uid}: {status}")


async def grant_extended_trial_for_users(session: AsyncSession, user_ids: list[int]) -> None:
    """Разрешает расширенный пробный период, если обычный ещё не использован."""
    if not user_ids:
        return
    rows = (
        await session.execute(
            update(User)
            .where(User.id.in_(user_ids), User.trial.in_([0, -1]))
            .values(trial=-1)
            .returning(User.id, User.tg_id)
        )
    ).all()
    keys = [
        key
        for uid, tg_id in rows
        for ref in ({uid, tg_id} - {None})
        for name in ("profile_data", "user_snapshot")
        for key in (cache_key(name, ref), user_ref_cache_key(name, UserId(ref)))
    ]
    if not defer_purge(session, *keys):
        for key in keys:
            await cache_delete(key)


async def get_trial(session: AsyncSession, legacy_user_ref: int) -> int:
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return 0
    result = await session.execute(select(func.coalesce(User.trial, 0)).where(User.id == uid))
    trial = result.scalar_one_or_none()
    return int(trial or 0)


async def get_balance_trial_key_count(session: AsyncSession, legacy_user_ref: int) -> tuple[float, int, int]:
    """Возвращает баланс, статус триала и число ключей клиента."""
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return 0.0, 0, 0
    key_count_subq = select(func.count()).select_from(Key).where(Key.user_id == User.id).scalar_subquery()
    result = await session.execute(
        select(
            func.coalesce(User.balance, 0.0),
            func.coalesce(User.trial, 0),
            key_count_subq,
        ).where(User.id == uid)
    )
    row = result.one_or_none()
    if row is None:
        return 0.0, 0, 0
    balance, trial, key_count = row
    return (
        round(float(balance or 0.0), 1),
        int(trial or 0),
        int(key_count or 0),
    )


async def upsert_user(
    session: AsyncSession,
    tg_id: int,
    username: str = None,
    first_name: str = None,
    last_name: str = None,
    language_code: str = None,
    is_bot: bool = False,
    only_if_exists: bool = False,
) -> dict | None:
    """Создаёт пользователя или обновляет поля профиля."""
    now = datetime.utcnow()
    returning_cols = list(User.__table__.c)

    if only_if_exists:
        username_value = username if username else User.username
        first_name_value = first_name if first_name else User.first_name
        last_name_value = last_name if last_name else User.last_name
        language_code_value = language_code if language_code else User.language_code

        res = await session.execute(
            update(User)
            .where(User.tg_id == tg_id)
            .values(
                username=username_value,
                first_name=first_name_value,
                last_name=last_name_value,
                language_code=language_code_value,
                is_bot=is_bot,
                updated_at=now,
            )
            .returning(*returning_cols)
        )
        row = res.mappings().one_or_none()
        if row is None:
            return None
        await cache_set(cache_key("user_exists", tg_id), True, USER_EXISTS_CACHE_TTL_SEC)
        return dict(row)

    res = await session.execute(
        insert(User)
        .values(
            tg_id=tg_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            is_bot=is_bot,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[User.tg_id],
            set_={
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "language_code": language_code,
                "is_bot": is_bot,
                "updated_at": now,
            },
        )
        .returning(*returning_cols)
    )
    row = res.mappings().one()
    await cache_set(cache_key("user_exists", tg_id), True, USER_EXISTS_CACHE_TTL_SEC)
    return dict(row)


async def delete_user_data(session: AsyncSession, legacy_user_ref: int):
    from database.keys import delete_key

    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    u = await session.scalar(
        select(User).where(User.id == u.id).with_for_update().execution_options(populate_existing=True)
    )
    if u is None:
        return
    uid = u.id
    tg_ref = u.tg_id

    identity_id = u.identity_id

    await session.execute(delete(Notification).where(Notification.user_id == uid))
    await session.execute(
        delete(GiftUsage).where(GiftUsage.gift_id.in_(select(Gift.gift_id).where(Gift.sender_user_id == uid)))
    )
    await session.execute(delete(GiftUsage).where(GiftUsage.user_id == uid))
    await session.execute(delete(Gift).where(Gift.sender_user_id == uid))
    await session.execute(
        update(Gift).where(Gift.recipient_user_id == uid).values(recipient_user_id=None, recipient_tg_id=None)
    )
    if u.tg_id is not None:
        await session.execute(update(Gift).where(Gift.recipient_tg_id == u.tg_id).values(recipient_tg_id=None))
        await session.execute(update(Gift).where(Gift.sender_tg_id == u.tg_id).values(sender_tg_id=None))
    payment_scope = Payment.user_id == uid
    if u.tg_id is not None:
        payment_scope |= and_(Payment.user_id.is_(None), Payment.tg_id == u.tg_id)
    receipts = (
        (
            await session.execute(
                select(Payment).where(payment_scope).with_for_update().execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for payment in receipts:
        payment.user_id = None
        payment.tg_id = None
        payment.metadata_ = {**(payment.metadata_ or {}), "owner_deleted": True}
    await session.execute(
        delete(Referral).where(or_(Referral.referrer_user_id == uid, Referral.referred_user_id == uid))
    )
    await session.execute(delete(CouponUsage).where(CouponUsage.user_id == uid))
    if u.tg_id is not None:
        await session.execute(update(Key).where(Key.tg_id == u.tg_id).values(tg_id=None))
    await delete_key(session, UserId(uid))
    await session.execute(delete(TemporaryData).where(TemporaryData.user_id == uid))
    await session.execute(delete(BlockedUser).where(BlockedUser.user_id == uid))
    await delete_recurring_user_data(session, uid)
    await delete_partner_user_data(session, uid)

    for model in (WebPushSubscription, WebNotification):
        scope = (model.user_id == uid) & model.identity_id.is_(None)
        if identity_id is not None:
            scope |= model.identity_id == identity_id
        await session.execute(delete(model).where(scope))
    await session.execute(
        update(ScheduledBroadcast).where(ScheduledBroadcast.created_by_user_id == uid).values(created_by_user_id=None)
    )

    await session.execute(delete(User).where(User.id == uid))

    if identity_id:
        still_linked = await session.scalar(
            select(func.count()).select_from(User).where(User.identity_id == identity_id)
        )
        if not still_linked:
            await session.execute(delete(Identity).where(Identity.id == identity_id))

    from database.access.resolution import invalidate_uid_cache
    from database.cache_purge import defer_purge

    refs = [uid] if tg_ref is None else [uid, tg_ref]
    keys = [
        cache_key(name, ref)
        for ref in refs
        for name in ("uref", "user_exists", "profile_data", "user_snapshot", "balance")
    ]
    keys.extend(user_ref_cache_keys(*refs))
    keys.extend(
        user_ref_cache_key(name, UserId(ref))
        for ref in refs
        for name in ("profile_data", "user_snapshot", "balance", "key_count", "keys_list")
    )
    if not defer_purge(session, *keys):
        await invalidate_uid_cache(*refs)
        for ref in refs:
            await invalidate_profile_cache(ref)
            await invalidate_balance_cache(ref)
            invalidate_user_snapshot(ref)

    logger.info(f"[DB] Данные пользователя id={uid} полностью удалены")


async def get_user_snapshot(session: AsyncSession, legacy_user_ref: int) -> tuple[int, int] | None:
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return None
    ckey = user_ref_cache_key("user_snapshot", UserId(uid))
    pending = is_purge_pending(session, ckey)
    cached = None if pending else await cache_get(ckey)
    if isinstance(cached, list) and len(cached) == 2:
        return (int(cached[0]), int(cached[1]))
    if isinstance(cached, tuple) and len(cached) == 2:
        return (int(cached[0]), int(cached[1]))
    keys_count_sq = select(func.count(Key.client_id)).where(Key.user_id == uid).scalar_subquery()
    res = await session.execute(select(func.coalesce(User.trial, 0), keys_count_sq).where(User.id == uid))
    row = res.first()
    if row is None:
        return None
    value = (int(row[0]), int(row[1]))
    if not pending:
        await cache_set(ckey, [value[0], value[1]], USER_SNAPSHOT_CACHE_TTL_SEC)
    return value


async def upsert_source_if_empty(
    session: AsyncSession,
    tg_id: int,
    source_code: str,
) -> bool:
    if not source_code:
        return False
    stmt = (
        insert(User)
        .values(tg_id=tg_id, source_code=source_code)
        .on_conflict_do_update(
            index_elements=["tg_id"],
            set_={"source_code": insert(User).excluded.source_code},
            where=(User.source_code.is_(None)),
        )
        .returning(User.tg_id)
    )
    res = await session.execute(stmt)
    changed_tg_id = res.scalar_one_or_none()
    return changed_tg_id is not None


async def get_user_language(session: AsyncSession, user_ref: int) -> str | None:
    """Возвращает язык клиента с учётом типа ссылки."""
    ref = user_ref if isinstance(user_ref, UserId) else TelegramId(user_ref)
    user = await resolve_user_optional(session, ref)
    return user.language_code if user is not None else None


async def get_billing_user_id_for_identity(
    session: AsyncSession, identity_id: str, legacy_tg_id: int | None = None
) -> UserId | None:
    """Находит расчётного клиента указанной идентичности."""
    user_id = await session.scalar(select(User.id).where(User.identity_id == identity_id))
    if user_id is None and legacy_tg_id is not None and int(legacy_tg_id) > 0:
        user_id = await session.scalar(
            select(User.id).where(
                User.tg_id == int(legacy_tg_id),
                (User.identity_id.is_(None)) | (User.identity_id == identity_id),
            )
        )
    return UserId(user_id) if user_id is not None else None


async def set_source_if_empty(session: AsyncSession, user_id: int, source_code: str) -> bool:
    """Заполняет пустой источник клиента по users.id."""
    if not source_code:
        return False
    res = await session.execute(
        update(User)
        .where(User.id == int(user_id), User.source_code.is_(None))
        .values(source_code=source_code)
        .returning(User.id)
    )
    return res.scalar_one_or_none() is not None
