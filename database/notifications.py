from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from config import DISCOUNT_ACTIVE_HOURS
from core.bootstrap import NOTIFICATIONS_CONFIG
from database.models import Key, Notification, User
from logger import logger


_NOTIFICATION_TIME_BATCH_SIZE = 300
_BULK_ADD_NOTIFICATIONS_BATCH_SIZE = 1000

async def add_notification(session: AsyncSession, tg_id: int, notification_type: str):
    try:
        stmt = (
            insert(Notification)
            .values(
                tg_id=tg_id,
                notification_type=notification_type,
                last_notification_time=datetime.utcnow(),
            )
            .on_conflict_do_update(
                index_elements=[Notification.tg_id, Notification.notification_type],
                set_={"last_notification_time": datetime.utcnow()},
            )
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"✅ Добавлено уведомление {notification_type} для пользователя {tg_id}")
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при добавлении уведомления: {e}")
        await session.rollback()
        raise


async def delete_notification(session: AsyncSession, tg_id: int, notification_type: str):
    await session.execute(
        delete(Notification).where(
            Notification.tg_id == tg_id,
            Notification.notification_type == notification_type,
        )
    )
    await session.commit()
    logger.debug(f"🗑 Уведомление {notification_type} для пользователя {tg_id} удалено")


async def bulk_add_notifications(
    session: AsyncSession, items: list[tuple[int, str]], *, commit: bool = False
) -> None:
    """Вставка/обновление многих (tg_id, notification_type) батчами (лимит параметров PostgreSQL). Без commit, если commit=False."""
    if not items:
        return
    now = datetime.utcnow()
    total = 0
    for i in range(0, len(items), _BULK_ADD_NOTIFICATIONS_BATCH_SIZE):
        batch = items[i : i + _BULK_ADD_NOTIFICATIONS_BATCH_SIZE]
        stmt = insert(Notification).values(
            [
                {"tg_id": tg_id, "notification_type": ntype, "last_notification_time": now}
                for tg_id, ntype in batch
            ]
        ).on_conflict_do_update(
            index_elements=[Notification.tg_id, Notification.notification_type],
            set_={"last_notification_time": now},
        )
        await session.execute(stmt)
        total += len(batch)
    if commit:
        await session.commit()
    logger.info(f"✅ Bulk: добавлено/обновлено {total} уведомлений")


INACTIVE_TRIAL_REGISTERED_TYPE = "inactive_trial_registered"


async def bulk_delete_notifications(
    session: AsyncSession, items: list[tuple[int, str]], *, commit: bool = False
) -> None:
    """Удаление многих (tg_id, notification_type) батчами (лимит параметров PostgreSQL). Без commit, если commit=False."""
    if not items:
        return
    total = 0
    for i in range(0, len(items), _BULK_ADD_NOTIFICATIONS_BATCH_SIZE):
        batch = items[i : i + _BULK_ADD_NOTIFICATIONS_BATCH_SIZE]
        stmt = delete(Notification).where(
            tuple_(Notification.tg_id, Notification.notification_type).in_(batch)
        )
        await session.execute(stmt)
        total += len(batch)
    if commit:
        await session.commit()
    logger.debug(f"🗑 Bulk: удалено {total} уведомлений")


async def check_notification_time(session: AsyncSession, tg_id: int, notification_type: str, hours: int = 12) -> bool:
    stmt = select(Notification.last_notification_time).where(
        Notification.tg_id == tg_id, Notification.notification_type == notification_type
    )
    result = await session.execute(stmt)
    last_time = result.scalar_one_or_none()
    if not last_time:
        return True
    return datetime.utcnow() - last_time > timedelta(hours=hours)


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
    now = datetime.utcnow()
    threshold = now - timedelta(hours=hours)
    can_notify = set()
    found = set()
    try:
        for batch in (
            items[i : i + _NOTIFICATION_TIME_BATCH_SIZE]
            for i in range(0, len(items), _NOTIFICATION_TIME_BATCH_SIZE)
        ):
            stmt = select(
                Notification.tg_id,
                Notification.notification_type,
                Notification.last_notification_time,
            ).where(tuple_(Notification.tg_id, Notification.notification_type).in_(batch))
            result = await session.execute(stmt)
            for row in result:
                found.add((row.tg_id, row.notification_type))
                if row.last_notification_time is None or row.last_notification_time < threshold:
                    can_notify.add((row.tg_id, row.notification_type))
        for pair in items:
            if pair not in found:
                can_notify.add(pair)
    except SQLAlchemyError:
        await session.rollback()
        raise
    return can_notify


async def get_last_notification_time(session: AsyncSession, tg_id: int, notification_type: str) -> int | None:
    stmt = select(Notification.last_notification_time).where(
        Notification.tg_id == tg_id, Notification.notification_type == notification_type
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
        stmt = select(
            Notification.tg_id,
            Notification.notification_type,
            Notification.last_notification_time,
        ).where(tuple_(Notification.tg_id, Notification.notification_type).in_(chunk))
        result = await session.execute(stmt)
        for tg_id, ntype, last_time in result.all():
            if last_time:
                out[(tg_id, ntype)] = int(last_time.timestamp() * 1000)
    return out


_HOT_LEAD_NOTIFICATION_TYPES = (
    "hot_lead_step_1",
    "hot_lead_step_2",
    "hot_lead_step_3",
    "hot_lead_step_2_expired",
)


async def get_hot_lead_notification_flags(
    session: AsyncSession, tg_ids: list[int]
) -> dict[int, set[str]]:
    """
    Один запрос: для каждого tg_id возвращает множество типов уведомлений hot_lead_*,
    которые у него уже есть. Используется в notify_hot_leads для устранения N+1.
    """
    if not tg_ids:
        return {}
    stmt = select(Notification.tg_id, Notification.notification_type).where(
        Notification.tg_id.in_(tg_ids),
        Notification.notification_type.in_(_HOT_LEAD_NOTIFICATION_TYPES),
    )
    result = await session.execute(stmt)
    out = defaultdict(set)
    for tg_id, ntype in result.all():
        out[tg_id].add(ntype)
    return dict(out)


async def check_hot_lead_discount(session: AsyncSession, tg_id: int) -> dict:
    try:
        result = await session.execute(
            select(Notification.notification_type, Notification.last_notification_time)
            .where(Notification.tg_id == tg_id)
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
        current_time = datetime.utcnow()

        if current_time > expires_at:
            return {"available": False}

        tariff_group = "discounts" if notification_type == "hot_lead_step_2" else "discounts_max"

        return {
            "available": True,
            "type": notification_type,
            "tariff_group": tariff_group,
            "expires_at": expires_at,
        }

    except Exception as e:
        logger.error(f"❌ Ошибка при проверке скидки горячего лида для {tg_id}: {e}")
        await session.rollback()
        return {"available": False}


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
    from sqlalchemy import select

    from database.models import BlockedUser, Notification

    try:
        now = datetime.utcnow()

        if notification_type == "inactive_trial":
            stmt_inactive = (
                select(User.tg_id)
                .where(
                    and_(
                        User.trial.in_([0, -1]),
                        ~User.tg_id.in_(select(BlockedUser.tg_id)),
                        ~User.tg_id.in_(select(Key.tg_id.distinct())),
                    )
                )
            )
            result_inactive = await session.execute(stmt_inactive)
            inactive_tg_ids = [r[0] for r in result_inactive.all()]
            if inactive_tg_ids:
                already = set()
                for chunk in _batched_list(inactive_tg_ids, _NOTIFICATION_TIME_BATCH_SIZE):
                    result_existing = await session.execute(
                        select(Notification.tg_id).where(
                            Notification.notification_type == INACTIVE_TRIAL_REGISTERED_TYPE,
                            Notification.tg_id.in_(chunk),
                        )
                    )
                    already.update(r[0] for r in result_existing.all())
                to_register = [tid for tid in inactive_tg_ids if tid not in already]
                if to_register:
                    for batch in _batched_list(to_register, _BULK_ADD_NOTIFICATIONS_BATCH_SIZE):
                        await bulk_add_notifications(
                            session,
                            [(tid, INACTIVE_TRIAL_REGISTERED_TYPE) for tid in batch],
                            commit=True,
                        )
                    logger.info(f"Зарегистрировано как неактивные (шаг 1): {len(to_register)} пользователей.")

            subq_registered = (
                select(
                    Notification.tg_id,
                    func.max(Notification.last_notification_time).label("registered_time"),
                )
                .where(Notification.notification_type == INACTIVE_TRIAL_REGISTERED_TYPE)
                .group_by(Notification.tg_id)
                .subquery()
            )
            subq_sent = (
                select(
                    Notification.tg_id,
                    func.max(Notification.last_notification_time).label("last_notification_time"),
                )
                .where(Notification.notification_type == notification_type)
                .group_by(Notification.tg_id)
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
                .outerjoin(Key, Key.tg_id == User.tg_id)
                .outerjoin(subq_registered, subq_registered.c.tg_id == User.tg_id)
                .outerjoin(subq_sent, subq_sent.c.tg_id == User.tg_id)
                .where(
                    and_(
                        User.trial.in_([0, -1]),
                        ~User.tg_id.in_(select(BlockedUser.tg_id)),
                        ~User.tg_id.in_(select(Key.tg_id.distinct())),
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
                    and (now - registered_time) >= timedelta(hours=hours)
                    and last_sent_time is None
                )
                second_ok = last_sent_time is not None and (now - last_sent_time) > timedelta(hours=hours)
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
            select(Notification.tg_id, func.max(Notification.last_notification_time).label("last_notification_time"))
            .where(Notification.notification_type == notification_type)
            .group_by(Notification.tg_id)
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
                .outerjoin(Key, Key.tg_id == User.tg_id)
                .outerjoin(subq_last_notification, subq_last_notification.c.tg_id == User.tg_id)
            )
            if tg_ids_batch:
                stmt = stmt.where(User.tg_id.in_(tg_ids_batch))
            if emails_batch:
                stmt = stmt.where(Key.email.in_(emails_batch))
            return stmt

        def _can_notify(last_time):
            return last_time is None or (now - last_time) > timedelta(hours=hours)

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

    except Exception as e:
        logger.error(f"Ошибка при массовой проверке уведомлений типа {notification_type}: {e}")
        await session.rollback()
        return []
