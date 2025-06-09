from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Key, Notification, User
from logger import logger


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
        logger.info(
            f"✅ Добавлено уведомление {notification_type} для пользователя {tg_id}"
        )
    except SQLAlchemyError as e:
        logger.error(f"❌ Ошибка при добавлении уведомления: {e}")
        await session.rollback()


async def delete_notification(
    session: AsyncSession, tg_id: int, notification_type: str
):
    await session.execute(
        delete(Notification).where(
            Notification.tg_id == tg_id,
            Notification.notification_type == notification_type,
        )
    )
    await session.commit()
    logger.info(f"🗑 Уведомление {notification_type} для пользователя {tg_id} удалено")


async def check_notification_time(
    session: AsyncSession, tg_id: int, notification_type: str, hours: int = 12
) -> bool:
    stmt = select(Notification.last_notification_time).where(
        Notification.tg_id == tg_id, Notification.notification_type == notification_type
    )
    result = await session.execute(stmt)
    last_time = result.scalar_one_or_none()
    if not last_time:
        return True
    return datetime.utcnow() - last_time > timedelta(hours=hours)


async def get_last_notification_time(
    session: AsyncSession, tg_id: int, notification_type: str
) -> int | None:
    stmt = select(Notification.last_notification_time).where(
        Notification.tg_id == tg_id, Notification.notification_type == notification_type
    )
    result = await session.execute(stmt)
    ts = result.scalar_one_or_none()
    if ts:
        return int(ts.timestamp() * 1000)
    return None


async def check_notifications_bulk(
    session: AsyncSession,
    notification_type: str,
    hours: int,
    tg_ids: list[int] = None,
    emails: list[str] = None,
) -> list[dict]:
    from sqlalchemy import and_, func, select
    from database.models import User, Key, Notification, BlockedUser

    try:
        now = datetime.utcnow()
        subq_last_notification = (
            select(
                Notification.tg_id,
                func.max(Notification.last_notification_time).label("last_notification_time")
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
                subq_last_notification.c.last_notification_time,
            )
            .outerjoin(Key, Key.tg_id == User.tg_id)
            .outerjoin(subq_last_notification, subq_last_notification.c.tg_id == User.tg_id)
        )

        if notification_type == "inactive_trial":
            stmt = stmt.where(
                and_(
                    User.trial.in_([0, -1]),
                    ~User.tg_id.in_(select(BlockedUser.tg_id)),
                    ~User.tg_id.in_(select(Key.tg_id.distinct()))
                )
            )

        if tg_ids:
            stmt = stmt.where(User.tg_id.in_(tg_ids))
        if emails:
            stmt = stmt.where(Key.email.in_(emails))

        result = await session.execute(stmt)
        users = []

        for row in result:
            last_time = row.last_notification_time
            can_notify = not last_time or (now - last_time > timedelta(hours=hours))

            if can_notify:
                users.append({
                    "tg_id": row.tg_id,
                    "email": row.email,
                    "username": row.username,
                    "first_name": row.first_name,
                    "last_name": row.last_name,
                    "last_notification_time": (
                        int(last_time.timestamp() * 1000) if last_time else None
                    )
                })

        logger.info(f"Найдено {len(users)} пользователей, готовых к уведомлению типа {notification_type}")
        return users

    except Exception as e:
        logger.error(f"Ошибка при массовой проверке уведомлений типа {notification_type}: {e}")
        return []
