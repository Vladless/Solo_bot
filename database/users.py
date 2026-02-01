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
    commit: bool = True,
) -> bool:
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
            .on_conflict_do_nothing(index_elements=["tg_id"])
            .returning(User.tg_id)
        )
        res = await session.execute(stmt)
        inserted_tg_id = res.scalar_one_or_none()
        if inserted_tg_id is None:
            return False
        if commit:
            await session.commit()
        logger.info(f"[DB] Новый пользователь добавлен: {tg_id} (source: {source_code})")
        return True
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
    balance = result.scalar_one_or_none()
    return round(float(balance or 0.0), 1)


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
    trial = result.scalar_one_or_none()
    return int(trial or 0)


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
    try:
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
            await session.commit()
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
        await session.commit()
        return dict(row)
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
    keys_count_sq = select(func.count(Key.client_id)).where(Key.tg_id == tg_id).scalar_subquery()

    res = await session.execute(select(func.coalesce(User.trial, 0), keys_count_sq).where(User.tg_id == tg_id))
    row = res.first()
    if row is None:
        return None
    return int(row[0]), int(row[1])


async def upsert_source_if_empty(
    session: AsyncSession,
    tg_id: int,
    source_code: str,
    commit: bool = True,
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
    if changed_tg_id is None:
        return False
    if commit:
        await session.commit()
    return True
