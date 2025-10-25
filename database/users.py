from datetime import datetime

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.keys import delete_key
from database.models import (
    BlockedUser,
    CouponUsage,
    Gift,
    GiftUsage,
    Key,
    Notification,
    Payment,
    Referral,
    TemporaryData,
    User,
)
from logger import logger


async def add_user(
    session: AsyncSession,
    tg_id: int,
    username: str = None,
    first_name: str = None,
    last_name: str = None,
    language_code: str = None,
    is_bot: bool = False,
    source_code: str = None,
):
    try:
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
            .on_conflict_do_nothing(index_elements=[User.tg_id])
        )
        await session.execute(stmt)
        await session.commit()
        logger.info(f"[DB] Новый пользователь добавлен: {tg_id} (source: {source_code})")
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при добавлении пользователя {tg_id}: {e}")
        await session.rollback()
        raise


async def update_balance(session: AsyncSession, tg_id: int, amount: float) -> None:
    try:
        res = await session.execute(
            update(User)
            .where(User.tg_id == tg_id)
            .values(balance=func.coalesce(User.balance, 0) + amount)
            .returning(User.balance)
        )
        new_balance = res.scalar_one_or_none()
        await session.commit()
        if new_balance is not None:
            old_balance = new_balance - amount
            logger.info(f"[DB] Баланс пользователя {tg_id} обновлён: {old_balance} → {new_balance}")
        else:
            logger.info(f"[DB] Баланс пользователя {tg_id} не изменён: пользователь не найден")
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при обновлении баланса пользователя {tg_id}: {e}")
        await session.rollback()


async def check_user_exists(session: AsyncSession, tg_id: int) -> bool:
    stmt = select(exists().where(User.tg_id == tg_id))
    result = await session.execute(stmt)
    return result.scalar()


async def get_balance(session: AsyncSession, tg_id: int) -> float:
    result = await session.execute(select(func.coalesce(User.balance, 0.0)).where(User.tg_id == tg_id))
    return round(float(result.scalar_one()), 1)


async def set_user_balance(session: AsyncSession, tg_id: int, balance: float) -> None:
    try:
        await session.execute(update(User).where(User.tg_id == tg_id).values(balance=balance))
        await session.commit()
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при установке баланса для пользователя {tg_id}: {e}")
        await session.rollback()


async def update_trial(session: AsyncSession, tg_id: int, status: int):
    try:
        await session.execute(update(User).where(User.tg_id == tg_id).values(trial=status))
        await session.commit()
        logger.info(f"[DB] Триал статус обновлён для пользователя {tg_id}: {status}")
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при обновлении триала пользователя {tg_id}: {e}")
        await session.rollback()


async def get_trial(session: AsyncSession, tg_id: int) -> int:
    result = await session.execute(select(func.coalesce(User.trial, 0)).where(User.tg_id == tg_id))
    return int(result.scalar_one())


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
    try:
        if only_if_exists:
            result = await session.execute(select(User).where(User.tg_id == tg_id))
            user = result.scalar_one_or_none()
            if not user:
                return None
            await session.execute(
                update(User)
                .where(User.tg_id == tg_id)
                .values(
                    username=username or user.username,
                    first_name=first_name or user.first_name,
                    last_name=last_name or user.last_name,
                    language_code=language_code or user.language_code,
                    is_bot=is_bot,
                    updated_at=datetime.utcnow(),
                )
            )
            await session.commit()
            result = await session.execute(select(User).where(User.tg_id == tg_id))
            return dict(result.scalar_one().__dict__)
        else:
            res = await session.execute(
                insert(User)
                .values(
                    tg_id=tg_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    language_code=language_code,
                    is_bot=is_bot,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                .on_conflict_do_update(
                    index_elements=[User.tg_id],
                    set_={
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name,
                        "language_code": language_code,
                        "is_bot": is_bot,
                        "updated_at": datetime.utcnow(),
                    },
                )
                .returning(User)
            )
            obj = res.scalar_one()
            await session.commit()
            d = obj.__dict__.copy()
            d.pop("_sa_instance_state", None)
            return d
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при UPSERT пользователя {tg_id}: {e}")
        await session.rollback()
        raise


async def delete_user_data(session: AsyncSession, tg_id: int):
    try:
        await session.execute(delete(Notification).where(Notification.tg_id == tg_id))
        await session.execute(
            delete(GiftUsage).where(GiftUsage.gift_id.in_(select(Gift.gift_id).where(Gift.sender_tg_id == tg_id)))
        )
        await session.execute(delete(Gift).where(Gift.sender_tg_id == tg_id))
        await session.execute(update(Gift).where(Gift.recipient_tg_id == tg_id).values(recipient_tg_id=None))
        await session.execute(delete(Payment).where(Payment.tg_id == tg_id))
        await session.execute(
            delete(Referral).where(or_(Referral.referrer_tg_id == tg_id, Referral.referred_tg_id == tg_id))
        )
        await session.execute(delete(CouponUsage).where(CouponUsage.user_id == tg_id))
        await delete_key(session, tg_id)
        await session.execute(delete(TemporaryData).where(TemporaryData.tg_id == tg_id))
        await session.execute(delete(BlockedUser).where(BlockedUser.tg_id == tg_id))
        await session.execute(delete(User).where(User.tg_id == tg_id))
        await session.commit()
        logger.info(f"[DB] Данные пользователя {tg_id} полностью удалены")
    except SQLAlchemyError as e:
        await session.rollback()
        logger.error(f"[DB] Ошибка при удалении данных пользователя {tg_id}: {e}")
        raise


async def mark_trial_extended(tg_id: int, session: AsyncSession):
    await session.execute(update(User).where(User.tg_id == tg_id).values(trial=-1))
    await session.commit()


async def get_user_snapshot(session: AsyncSession, tg_id: int) -> tuple[int, int] | None:
    res = await session.execute(
        select(func.coalesce(User.trial, 0), func.count(Key.client_id))
        .select_from(User)
        .join(Key, Key.tg_id == User.tg_id, isouter=True)
        .where(User.tg_id == tg_id)
        .group_by(User.tg_id, User.trial)
    )
    row = res.first()
    if row is None:
        return None
    return int(row[0]), int(row[1])


async def upsert_source_if_empty(session: AsyncSession, tg_id: int, source_code: str) -> None:
    if not source_code:
        return
    stmt = (
        insert(User)
        .values(tg_id=tg_id, source_code=source_code)
        .on_conflict_do_update(
            index_elements=[User.tg_id],
            set_={"source_code": insert(User).excluded.source_code},
            where=(User.source_code.is_(None)),
        )
    )
    await session.execute(stmt)
    await session.commit()
