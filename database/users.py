from datetime import datetime

from sqlalchemy import and_, delete, func, not_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.redis_cache import cache_delete, cache_get, cache_key, cache_set
from database.access.resolution import resolve_uid_cached, resolve_user_optional
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
        loop = asyncio.get_running_loop()
        loop.create_task(cache_delete(cache_key("user_snapshot", tg_id)))
    except RuntimeError:
        return


def exclude_shadow_placeholders():
    """Фильтр для статистики: убирает записи, заведённые массовым теневым баном.

    Такой клиент создаётся только ради внешнего ключа бана и ни разу не заходил
    в бота — профиль пустой. Реальные клиенты, забаненные позже, остаются.
    """
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
    await cache_set(cache_key("user_exists", tg_id), True, USER_EXISTS_CACHE_TTL_SEC)
    logger.info(f"[DB] Новый пользователь добавлен: tg_id={tg_id} id={inserted_id} (source: {source_code})")
    return int(inserted_id)


async def invalidate_balance_cache(tg_id: int) -> None:
    await cache_delete(cache_key("balance", tg_id))


async def invalidate_profile_cache(tg_id: int) -> None:
    await cache_delete(cache_key("profile_data", tg_id))


async def update_balance(
    session: AsyncSession,
    legacy_user_ref: int,
    amount: float,
) -> float | None:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        logger.info(f"[DB] Баланс не изменён: пользователь {legacy_user_ref} не найден")
        return None
    uid = u.id
    amount = float(amount)
    stmt = update(User).values(balance=func.coalesce(User.balance, 0) + amount).returning(User.balance)
    if amount < 0:
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
    await invalidate_balance_cache(uid)
    await invalidate_profile_cache(uid)
    if u.tg_id is not None:
        await invalidate_balance_cache(u.tg_id)
        await invalidate_profile_cache(u.tg_id)
    return float(new_balance)


async def check_user_exists(session: AsyncSession, legacy_user_ref: int) -> bool:
    cached = await cache_get(cache_key("user_exists", legacy_user_ref))
    if isinstance(cached, bool):
        return cached
    u = await resolve_user_optional(session, legacy_user_ref)
    value = u is not None
    await cache_set(cache_key("user_exists", legacy_user_ref), value, USER_EXISTS_CACHE_TTL_SEC)
    return value


async def get_balance(session: AsyncSession, legacy_user_ref: int) -> float:
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return 0.0
    cached = await cache_get(cache_key("balance", uid))
    if cached is not None:
        try:
            return round(float(cached), 1)
        except (TypeError, ValueError):
            pass
    result = await session.execute(select(func.coalesce(User.balance, 0.0)).where(User.id == uid))
    balance = result.scalar_one_or_none()
    value = round(float(balance or 0.0), 1)
    await cache_set(cache_key("balance", uid), value, BALANCE_CACHE_TTL_SEC)
    return value


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
    await invalidate_balance_cache(uid)
    await invalidate_profile_cache(uid)


async def get_user_preferred_currency(session: AsyncSession, tg_id: int) -> str | None:
    """Предпочитаемая валюта пользователя по ``tg_id``, если установлена."""
    ckey = cache_key("pref_ccy", tg_id)
    cached = await cache_get(ckey)
    if isinstance(cached, str):
        return cached or None
    result = await session.execute(select(User.preferred_currency).where(User.tg_id == int(tg_id)))
    value = result.scalar()
    await cache_set(ckey, value or "", PREFERRED_CURRENCY_CACHE_TTL_SEC)
    return value


async def mark_trial_started_if_eligible(session: AsyncSession, tg_id: int) -> None:
    """Переводит `trial` в 1, только если текущее значение in [0, -1] (пользователь
    ещё не использовал триал). Условный update без пред-чтения — атомарно на уровне БД.

    Используется в `services.operations.creation.create_key_on_cluster` после
    успешного создания ключа.
    """
    await session.execute(update(User).where(User.tg_id == tg_id, User.trial.in_([0, -1])).values(trial=1))


async def update_trial(session: AsyncSession, legacy_user_ref: int, status: int):
    from database.cache_purge import defer_purge

    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    uid = u.id
    await session.execute(update(User).where(User.id == uid).values(trial=status))
    refs = [uid] if u.tg_id is None else [uid, u.tg_id]
    keys = [cache_key(name, ref) for ref in refs for name in ("profile_data", "user_snapshot")]
    if not defer_purge(session, *keys):
        await invalidate_profile_cache(uid)
        invalidate_user_snapshot(uid)
        if u.tg_id is not None:
            await invalidate_profile_cache(u.tg_id)
            invalidate_user_snapshot(u.tg_id)
    logger.info(f"[DB] Триал статус обновлён для пользователя id={uid}: {status}")


async def get_trial(session: AsyncSession, legacy_user_ref: int) -> int:
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return 0
    result = await session.execute(select(func.coalesce(User.trial, 0)).where(User.id == uid))
    trial = result.scalar_one_or_none()
    return int(trial or 0)


async def get_balance_and_trial(session: AsyncSession, legacy_user_ref: int) -> tuple[float, int]:
    """Один запрос к БД для баланса и триала (профиль при промахе кэша)."""
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return 0.0, 0
    result = await session.execute(
        select(
            func.coalesce(User.balance, 0.0),
            func.coalesce(User.trial, 0),
        ).where(User.id == uid)
    )
    row = result.one_or_none()
    if row is None:
        return 0.0, 0
    balance, trial = row
    return round(float(balance or 0.0), 1), int(trial or 0)


async def get_balance_trial_key_count(session: AsyncSession, legacy_user_ref: int) -> tuple[float, int, int]:
    """
    Один запрос: баланс, триал и число ключей пользователя (для профиля при промахе кэша).
    Возвращает (balance_rub, trial_status, key_count).
    """
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
    uid = u.id

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
        await session.execute(update(Payment).where(Payment.tg_id == u.tg_id).values(tg_id=None))
    await session.execute(delete(Payment).where(Payment.user_id == uid))
    await session.execute(
        delete(Referral).where(or_(Referral.referrer_user_id == uid, Referral.referred_user_id == uid))
    )
    await session.execute(delete(CouponUsage).where(CouponUsage.user_id == uid))
    if u.tg_id is not None:
        await session.execute(update(Key).where(Key.tg_id == u.tg_id).values(tg_id=None))
    await delete_key(session, uid)
    await session.execute(
        delete(TemporaryData).where(
            or_(
                TemporaryData.user_id == uid,
                TemporaryData.tg_id == u.tg_id,
            )
        )
    )
    await session.execute(
        delete(BlockedUser).where(
            or_(
                BlockedUser.user_id == uid,
                BlockedUser.tg_id == u.tg_id,
            )
        )
    )

    await session.execute(delete(WebPushSubscription).where(WebPushSubscription.user_id == uid))
    await session.execute(delete(WebNotification).where(WebNotification.user_id == uid))
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

    logger.info(f"[DB] Данные пользователя id={uid} полностью удалены")


async def mark_trial_extended(legacy_user_ref: int, session: AsyncSession):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    await session.execute(update(User).where(User.id == u.id).values(trial=-1))
    invalidate_user_snapshot(u.id)
    if u.tg_id is not None:
        invalidate_user_snapshot(u.tg_id)


async def get_user_snapshot(session: AsyncSession, legacy_user_ref: int) -> tuple[int, int] | None:
    uid = await resolve_uid_cached(session, legacy_user_ref)
    if uid is None:
        return None
    cached = await cache_get(cache_key("user_snapshot", uid))
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
    await cache_set(cache_key("user_snapshot", uid), [value[0], value[1]], USER_SNAPSHOT_CACHE_TTL_SEC)
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
