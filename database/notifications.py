from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.bootstrap import NOTIFICATIONS_CONFIG
from database.access.resolution import resolve_user_optional
from database.models import BlockedUser, Key, Notification, User
from logger import logger
from settings.config import DISCOUNT_ACTIVE_HOURS


_NOTIFICATION_TIME_BATCH_SIZE = 300
_BULK_ADD_NOTIFICATIONS_BATCH_SIZE = 1000
_LEGACY_REF_MAP_BATCH_SIZE = 5000


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _map_legacy_refs_to_user_ids(session: AsyncSession, refs: list[int]) -> dict[int, int]:
    if not refs:
        return {}
    from sqlalchemy import or_

    uniq = list(dict.fromkeys(refs))
    m: dict[int, int] = {}
    for i in range(0, len(uniq), _LEGACY_REF_MAP_BATCH_SIZE):
        chunk = uniq[i : i + _LEGACY_REF_MAP_BATCH_SIZE]
        r = await session.execute(select(User.id, User.tg_id).where(or_(User.tg_id.in_(chunk), User.id.in_(chunk))))
        for uid, tgid in r.all():
            m[int(uid)] = int(uid)
            if tgid is not None:
                m[int(tgid)] = int(uid)
    return {ref: m[ref] for ref in uniq if ref in m}


async def add_notification(
    session: AsyncSession,
    legacy_user_ref: int,
    notification_type: str,
    *,
    commit: bool = True,
):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    ins = insert(Notification).values(
        user_id=u.id,
        tg_id=u.tg_id,
        notification_type=notification_type,
        last_notification_time=_utc_now(),
    )
    stmt = ins.on_conflict_do_update(
        index_elements=[Notification.user_id, Notification.notification_type],
        set_={
            "last_notification_time": ins.excluded.last_notification_time,
            "tg_id": ins.excluded.tg_id,
        },
    )
    await session.execute(stmt)
    if commit:
        try:
            await session.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка commit add_notification {notification_type} для {u.id}: {e}")
            await session.rollback()
            return
    logger.info(f"✅ Добавлено уведомление {notification_type} для пользователя {u.id}")


async def delete_notification(
    session: AsyncSession,
    legacy_user_ref: int,
    notification_type: str,
    *,
    commit: bool = True,
):
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return
    uid = u.id
    await session.execute(
        delete(Notification).where(
            Notification.user_id == uid,
            Notification.notification_type == notification_type,
        )
    )
    if commit:
        try:
            await session.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка commit delete_notification {notification_type} для {uid}: {e}")
            await session.rollback()
            return
    logger.debug(f"🗑 Уведомление {notification_type} для пользователя {uid} удалено")


async def bulk_add_notifications(
    session: AsyncSession,
    items: list[tuple[int, str]],
    *,
    commit: bool = False,
) -> None:
    """Вставка/обновление многих (legacy_user_ref, notification_type) батчами (лимит параметров PostgreSQL)."""
    if not items:
        return
    id_map = await _map_legacy_refs_to_user_ids(session, [p[0] for p in items])
    mapped = [(id_map[r], n) for r, n in items if r in id_map]
    if not mapped:
        return
    uids = list({uid for uid, _ in mapped})
    tg_by_uid: dict[int, int | None] = {}
    for i in range(0, len(uids), _LEGACY_REF_MAP_BATCH_SIZE):
        chunk = uids[i : i + _LEGACY_REF_MAP_BATCH_SIZE]
        tg_map_r = await session.execute(select(User.id, User.tg_id).where(User.id.in_(chunk)))
        for row in tg_map_r.all():
            tg_by_uid[int(row.id)] = row.tg_id
    now = _utc_now()
    total = 0
    for i in range(0, len(mapped), _BULK_ADD_NOTIFICATIONS_BATCH_SIZE):
        batch = mapped[i : i + _BULK_ADD_NOTIFICATIONS_BATCH_SIZE]
        ins = insert(Notification).values([
            {
                "user_id": uid,
                "tg_id": tg_by_uid.get(uid),
                "notification_type": ntype,
                "last_notification_time": now,
            }
            for uid, ntype in batch
        ])
        stmt = ins.on_conflict_do_update(
            index_elements=[Notification.user_id, Notification.notification_type],
            set_={
                "last_notification_time": ins.excluded.last_notification_time,
                "tg_id": ins.excluded.tg_id,
            },
        )
        await session.execute(stmt)
        total += len(batch)
    if commit:
        try:
            await session.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка commit bulk_add_notifications: {e}")
            await session.rollback()
            return
    logger.info(f"✅ Bulk: добавлено/обновлено {total} уведомлений")


INACTIVE_TRIAL_REGISTERED_TYPE = "inactive_trial_registered"


async def bulk_delete_notifications(
    session: AsyncSession,
    items: list[tuple[int, str]],
    *,
    commit: bool = False,
) -> None:
    """Удаление многих (legacy_user_ref, notification_type) батчами (лимит параметров PostgreSQL)."""
    if not items:
        return
    id_map = await _map_legacy_refs_to_user_ids(session, [p[0] for p in items])
    mapped = [(id_map[r], n) for r, n in items if r in id_map]
    if not mapped:
        return
    total = 0
    for i in range(0, len(mapped), _BULK_ADD_NOTIFICATIONS_BATCH_SIZE):
        batch = mapped[i : i + _BULK_ADD_NOTIFICATIONS_BATCH_SIZE]
        stmt = delete(Notification).where(tuple_(Notification.user_id, Notification.notification_type).in_(batch))
        await session.execute(stmt)
        total += len(batch)
    if commit:
        try:
            await session.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка commit bulk_delete_notifications: {e}")
            await session.rollback()
            return
    logger.debug(f"🗑 Bulk: удалено {total} уведомлений")


async def check_notification_time(
    session: AsyncSession, legacy_user_ref: int, notification_type: str, hours: int = 12
) -> bool:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return True
    stmt = select(Notification.last_notification_time).where(
        Notification.user_id == u.id, Notification.notification_type == notification_type
    )
    result = await session.execute(stmt)
    last_time = result.scalar_one_or_none()
    if not last_time:
        return True
    return _utc_now() - _as_utc(last_time) > timedelta(hours=hours)


async def check_notification_time_bulk(
    session: AsyncSession,
    items: list[tuple[int, str]],
    hours: int,
) -> set[tuple[int, str]]:
    """
    Определяет, кому из (tg_id, notification_type) можно слать уведомление
    (прошло больше hours с последней отправки или не слали никогда).
    Обрабатывает items батчами, чтобы не превышать лимит параметров в одном запросе.
    Возвращает множество пар (tg_id, notification_type), которым можно слать.
    """
    if not items:
        return set()
    now = _utc_now()
    threshold = now - timedelta(hours=hours)
    can_notify = set()
    found = set()
    for batch in (
        items[i : i + _NOTIFICATION_TIME_BATCH_SIZE] for i in range(0, len(items), _NOTIFICATION_TIME_BATCH_SIZE)
    ):
        id_map = await _map_legacy_refs_to_user_ids(session, [p[0] for p in batch])
        mapped_batch = [(id_map[r], n) for r, n in batch if r in id_map]
        if not mapped_batch:
            continue
        stmt = select(
            Notification.user_id,
            Notification.notification_type,
            Notification.last_notification_time,
        ).where(tuple_(Notification.user_id, Notification.notification_type).in_(mapped_batch))
        result = await session.execute(stmt)
        uid_to_ref: dict[int, int] = {}
        for r, _n in batch:
            if r in id_map:
                uid_to_ref[id_map[r]] = r
        for row in result:
            ref = uid_to_ref.get(row.user_id, row.user_id)
            found.add((ref, row.notification_type))
            row_time = _as_utc(row.last_notification_time)
            if row_time is None or row_time < threshold:
                can_notify.add((ref, row.notification_type))
    for pair in items:
        if pair not in found:
            can_notify.add(pair)
    return can_notify


async def get_last_notification_time(session: AsyncSession, legacy_user_ref: int, notification_type: str) -> int | None:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return None
    stmt = select(Notification.last_notification_time).where(
        Notification.user_id == u.id, Notification.notification_type == notification_type
    )
    result = await session.execute(stmt)
    ts = result.scalar_one_or_none()
    if ts:
        return int(ts.timestamp() * 1000)
    return None


async def get_last_notification_times_bulk(
    session: AsyncSession, pairs: list[tuple[int, str]]
) -> dict[tuple[int, str], int]:
    """
    Один запрос: последние времена уведомлений для списка (tg_id, notification_type).
    Возвращает dict[(tg_id, notification_type)] -> timestamp_ms.
    """
    if not pairs:
        return {}
    from sqlalchemy import tuple_

    out = {}
    for chunk in _batched_list(pairs, _BULK_NOTIFICATION_BATCH_SIZE):
        id_map = await _map_legacy_refs_to_user_ids(session, [p[0] for p in chunk])
        mapped = [(id_map[r], n) for r, n in chunk if r in id_map]
        if not mapped:
            continue
        stmt = select(
            Notification.user_id,
            Notification.notification_type,
            Notification.last_notification_time,
        ).where(tuple_(Notification.user_id, Notification.notification_type).in_(mapped))
        result = await session.execute(stmt)
        uid_to_ref: dict[int, int] = {}
        for r, _n in chunk:
            if r in id_map:
                uid_to_ref[id_map[r]] = r
        for uid, ntype, last_time in result.all():
            if last_time:
                ref = uid_to_ref.get(uid, uid)
                out[(ref, ntype)] = int(last_time.timestamp() * 1000)
    return out


_HOT_LEAD_NOTIFICATION_TYPES = (
    "hot_lead_step_1",
    "hot_lead_step_2",
    "hot_lead_step_3",
    "hot_lead_step_2_expired",
)

_COLD_LEAD_NOTIFICATION_TYPES = (
    "cold_lead_step_1",
    "cold_lead_step_2",
    "cold_lead_step_3",
)


async def get_hot_lead_notification_flags(session: AsyncSession, legacy_user_refs: list[int]) -> dict[int, set[str]]:
    """
    Один запрос: для каждого legacy_user_ref (tg_id или user_id) возвращает множество
    типов уведомлений hot_lead_*, которые у пользователя уже есть.
    """
    if not legacy_user_refs:
        return {}
    id_map = await _map_legacy_refs_to_user_ids(session, legacy_user_refs)
    if not id_map:
        return {}
    uids = list(set(id_map.values()))
    uid_to_ref = {id_map[ref]: ref for ref in legacy_user_refs if ref in id_map}
    out = defaultdict(set)
    for chunk in _batched_list(uids, _LEGACY_REF_MAP_BATCH_SIZE):
        stmt = select(Notification.user_id, Notification.notification_type).where(
            Notification.user_id.in_(chunk),
            Notification.notification_type.in_(_HOT_LEAD_NOTIFICATION_TYPES),
        )
        result = await session.execute(stmt)
        for uid, ntype in result.all():
            out[uid_to_ref.get(uid, uid)].add(ntype)
    return dict(out)


async def get_cold_lead_notification_flags(session: AsyncSession, legacy_user_refs: list[int]) -> dict[int, set[str]]:
    if not legacy_user_refs:
        return {}
    id_map = await _map_legacy_refs_to_user_ids(session, legacy_user_refs)
    if not id_map:
        return {}
    uids = list(set(id_map.values()))
    uid_to_ref = {id_map[ref]: ref for ref in legacy_user_refs if ref in id_map}
    out = defaultdict(set)
    for chunk in _batched_list(uids, _LEGACY_REF_MAP_BATCH_SIZE):
        stmt = select(Notification.user_id, Notification.notification_type).where(
            Notification.user_id.in_(chunk),
            Notification.notification_type.in_(_COLD_LEAD_NOTIFICATION_TYPES),
        )
        result = await session.execute(stmt)
        for uid, ntype in result.all():
            out[uid_to_ref.get(uid, uid)].add(ntype)
    return dict(out)


async def check_hot_lead_discount(session: AsyncSession, legacy_user_ref: int) -> dict:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return {"available": False}
    result = await session.execute(
        select(Notification.notification_type, Notification.last_notification_time)
        .where(Notification.user_id == u.id)
        .where(Notification.notification_type.in_(["hot_lead_step_2", "hot_lead_step_3"]))
        .order_by(Notification.last_notification_time.desc())
        .limit(1)
    )

    row = result.first()
    if not row:
        return {"available": False}

    notification_type, last_time = row

    hours = int(NOTIFICATIONS_CONFIG.get("DISCOUNT_ACTIVE_HOURS", DISCOUNT_ACTIVE_HOURS))

    expires_at = last_time + timedelta(hours=hours)
    current_time = _utc_now()

    if current_time > expires_at:
        return {"available": False}

    tariff_group = "discounts" if notification_type == "hot_lead_step_2" else "discounts_max"

    return {
        "available": True,
        "type": notification_type,
        "tariff_group": tariff_group,
        "expires_at": expires_at,
    }


async def check_cold_lead_discount(session: AsyncSession, legacy_user_ref: int) -> dict:
    u = await resolve_user_optional(session, legacy_user_ref)
    if u is None:
        return {"available": False}
    result = await session.execute(
        select(Notification.notification_type, Notification.last_notification_time)
        .where(Notification.user_id == u.id)
        .where(Notification.notification_type.in_(["cold_lead_step_2", "cold_lead_step_3"]))
        .order_by(Notification.last_notification_time.desc())
        .limit(1)
    )

    row = result.first()
    if not row:
        return {"available": False}

    notification_type, last_time = row

    hours = int(NOTIFICATIONS_CONFIG.get("DISCOUNT_ACTIVE_HOURS", DISCOUNT_ACTIVE_HOURS))

    expires_at = last_time + timedelta(hours=hours)
    current_time = _utc_now()

    if current_time > expires_at:
        return {"available": False}

    tariff_group = "cold_discounts" if notification_type == "cold_lead_step_2" else "cold_discounts_max"

    return {
        "available": True,
        "type": notification_type,
        "tariff_group": tariff_group,
        "expires_at": expires_at,
    }


_BULK_NOTIFICATION_BATCH_SIZE = 250


def _batched_pairs(tg_ids: list[int], emails: list[str], batch_size: int):
    """Yield (tg_ids_chunk, emails_chunk) of length <= batch_size. Lists must have same length."""
    for i in range(0, len(tg_ids), batch_size):
        yield tg_ids[i : i + batch_size], emails[i : i + batch_size]


def _batched_list(items: list, batch_size: int):
    """Yield chunks of items of length <= batch_size."""
    for i in range(0, len(items), batch_size):
        yield items[i : i + batch_size]


async def check_notifications_bulk(
    session: AsyncSession,
    notification_type: str,
    hours: int,
    tg_ids: list[int] = None,
    emails: list[str] = None,
) -> list[dict]:
    now = _utc_now()

    if notification_type == "inactive_trial":
        stmt_inactive = select(User.id).where(
            and_(
                User.trial.in_([0, -1]),
                User.tg_id.isnot(None),
                ~User.id.in_(select(BlockedUser.user_id)),
                ~User.id.in_(select(Key.user_id.distinct())),
            )
        )
        result_inactive = await session.execute(stmt_inactive)
        inactive_user_ids = [r[0] for r in result_inactive.all()]
        if inactive_user_ids:
            already = set()
            for chunk in _batched_list(inactive_user_ids, _NOTIFICATION_TIME_BATCH_SIZE):
                result_existing = await session.execute(
                    select(Notification.user_id).where(
                        Notification.notification_type == INACTIVE_TRIAL_REGISTERED_TYPE,
                        Notification.user_id.in_(chunk),
                    )
                )
                already.update(r[0] for r in result_existing.all())
            to_register = [uid for uid in inactive_user_ids if uid not in already]
            if to_register:
                for batch in _batched_list(to_register, _BULK_ADD_NOTIFICATIONS_BATCH_SIZE):
                    await bulk_add_notifications(
                        session,
                        [(uid, INACTIVE_TRIAL_REGISTERED_TYPE) for uid in batch],
                        commit=True,
                    )
                logger.info(f"Зарегистрировано как неактивные (шаг 1): {len(to_register)} пользователей.")

        subq_registered = (
            select(
                Notification.user_id,
                func.max(Notification.last_notification_time).label("registered_time"),
            )
            .where(Notification.notification_type == INACTIVE_TRIAL_REGISTERED_TYPE)
            .group_by(Notification.user_id)
            .subquery()
        )
        subq_sent = (
            select(
                Notification.user_id,
                func.max(Notification.last_notification_time).label("last_notification_time"),
            )
            .where(Notification.notification_type == notification_type)
            .group_by(Notification.user_id)
            .subquery()
        )
        stmt = (
            select(
                User.tg_id,
                Key.email,
                User.username,
                User.first_name,
                User.last_name,
                subq_registered.c.registered_time,
                subq_sent.c.last_notification_time,
            )
            .select_from(User)
            .outerjoin(Key, Key.user_id == User.id)
            .outerjoin(subq_registered, subq_registered.c.user_id == User.id)
            .outerjoin(subq_sent, subq_sent.c.user_id == User.id)
            .where(
                and_(
                    User.trial.in_([0, -1]),
                    User.tg_id.isnot(None),
                    ~User.id.in_(select(BlockedUser.user_id)),
                    ~User.id.in_(select(Key.user_id.distinct())),
                )
            )
        )
        result = await session.execute(stmt)
        users = []
        for row in result:
            registered_time = row.registered_time
            last_sent_time = row.last_notification_time
            first_ok = (
                registered_time is not None
                and (now - _as_utc(registered_time)) >= timedelta(hours=hours)
                and last_sent_time is None
            )
            second_ok = last_sent_time is not None and (now - _as_utc(last_sent_time)) > timedelta(hours=hours)
            if first_ok or second_ok:
                users.append({
                    "tg_id": row.tg_id,
                    "email": row.email,
                    "username": row.username,
                    "first_name": row.first_name,
                    "last_name": row.last_name,
                    "last_notification_time": int(last_sent_time.timestamp() * 1000) if last_sent_time else None,
                })
        logger.info(f"Найдено {len(users)} пользователей, готовых к уведомлению типа {notification_type}")
        return users

    subq_last_notification = (
        select(Notification.user_id, func.max(Notification.last_notification_time).label("last_notification_time"))
        .where(Notification.notification_type == notification_type)
        .group_by(Notification.user_id)
        .subquery()
    )

    def make_stmt(tg_ids_batch: list[int] | None, emails_batch: list[str] | None):
        stmt = (
            select(
                User.tg_id,
                Key.email,
                User.username,
                User.first_name,
                User.last_name,
                subq_last_notification.c.last_notification_time,
            )
            .select_from(User)
            .outerjoin(Key, Key.user_id == User.id)
            .outerjoin(subq_last_notification, subq_last_notification.c.user_id == User.id)
        )
        if tg_ids_batch:
            stmt = stmt.where(User.tg_id.in_(tg_ids_batch))
        if emails_batch:
            stmt = stmt.where(Key.email.in_(emails_batch))
        return stmt

    def _can_notify(last_time):
        return last_time is None or (now - _as_utc(last_time)) > timedelta(hours=hours)

    users: list[dict] = []
    seen: set[tuple[int, str | None]] = set()

    if tg_ids and emails and len(tg_ids) == len(emails):
        for tg_ids_chunk, emails_chunk in _batched_pairs(tg_ids, emails, _BULK_NOTIFICATION_BATCH_SIZE):
            stmt = make_stmt(tg_ids_chunk, emails_chunk)
            result = await session.execute(stmt)
            for row in result:
                key = (row.tg_id, row.email)
                if key in seen:
                    continue
                seen.add(key)
                last_time = row.last_notification_time
                if _can_notify(last_time):
                    users.append({
                        "tg_id": row.tg_id,
                        "email": row.email,
                        "username": row.username,
                        "first_name": row.first_name,
                        "last_name": row.last_name,
                        "last_notification_time": int(last_time.timestamp() * 1000) if last_time else None,
                    })
    elif tg_ids and emails:
        for tg_ids_chunk in _batched_list(tg_ids, _BULK_NOTIFICATION_BATCH_SIZE):
            for emails_chunk in _batched_list(emails, _BULK_NOTIFICATION_BATCH_SIZE):
                stmt = make_stmt(tg_ids_chunk, emails_chunk)
                result = await session.execute(stmt)
                for row in result:
                    key = (row.tg_id, row.email)
                    if key in seen:
                        continue
                    seen.add(key)
                    last_time = row.last_notification_time
                    if _can_notify(last_time):
                        users.append({
                            "tg_id": row.tg_id,
                            "email": row.email,
                            "username": row.username,
                            "first_name": row.first_name,
                            "last_name": row.last_name,
                            "last_notification_time": int(last_time.timestamp() * 1000) if last_time else None,
                        })
    elif tg_ids:
        for tg_ids_chunk in _batched_list(tg_ids, _BULK_NOTIFICATION_BATCH_SIZE):
            stmt = make_stmt(tg_ids_chunk, None)
            result = await session.execute(stmt)
            for row in result:
                key = (row.tg_id, row.email)
                if key in seen:
                    continue
                seen.add(key)
                last_time = row.last_notification_time
                if _can_notify(last_time):
                    users.append({
                        "tg_id": row.tg_id,
                        "email": row.email,
                        "username": row.username,
                        "first_name": row.first_name,
                        "last_name": row.last_name,
                        "last_notification_time": int(last_time.timestamp() * 1000) if last_time else None,
                    })
    elif emails:
        for emails_chunk in _batched_list(emails, _BULK_NOTIFICATION_BATCH_SIZE):
            stmt = make_stmt(None, emails_chunk)
            result = await session.execute(stmt)
            for row in result:
                key = (row.tg_id, row.email)
                if key in seen:
                    continue
                seen.add(key)
                last_time = row.last_notification_time
                if _can_notify(last_time):
                    users.append({
                        "tg_id": row.tg_id,
                        "email": row.email,
                        "username": row.username,
                        "first_name": row.first_name,
                        "last_name": row.last_name,
                        "last_notification_time": int(last_time.timestamp() * 1000) if last_time else None,
                    })
    else:
        stmt = make_stmt(None, None)
        result = await session.execute(stmt)
        for row in result:
            last_time = row.last_notification_time
            if _can_notify(last_time):
                users.append({
                    "tg_id": row.tg_id,
                    "email": row.email,
                    "username": row.username,
                    "first_name": row.first_name,
                    "last_name": row.last_name,
                    "last_notification_time": int(last_time.timestamp() * 1000) if last_time else None,
                })

    logger.info(f"Найдено {len(users)} пользователей, готовых к уведомлению типа {notification_type}")
    return users
