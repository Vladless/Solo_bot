from datetime import datetime, timedelta

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from database.keys import delete_key
from database.models import (
    BalanceHistory,
    BlockedUser,
    CouponUsage,
    Gift,
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
        logger.info(
            f"[DB] Новый пользователь добавлен: {tg_id} (source: {source_code})"
        )
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при добавлении пользователя {tg_id}: {e}")
        await session.rollback()
        raise


async def update_balance(
    session: AsyncSession, 
    tg_id: int, 
    amount: float, 
    operation_type: str = 'manual',
    description: str = None,
    admin_id: int = None
) -> None:
    try:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User {tg_id} not found")
            
        current_balance = user.balance or 0
        new_balance = current_balance + amount
        
        # Update user balance
        user.balance = new_balance
        
        # Log the balance change
        balance_history = BalanceHistory(
            tg_id=tg_id,
            amount=amount,
            balance_before=current_balance,
            balance_after=new_balance,
            operation_type=operation_type,
            description=description,
            admin_id=admin_id
        )
        session.add(balance_history)
        
        await session.commit()
        logger.info(
            f"[DB] Баланс пользователя {tg_id} обновлён: {current_balance} → {new_balance} "
            f"(операция: {operation_type}, описание: {description or 'нет'})"
        )
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при обновлении баланса пользователя {tg_id}: {e}")
        await session.rollback()
        raise


async def update_referral_balance(
    session: AsyncSession, 
    tg_id: int, 
    amount: float,
    operation_type: str = 'referral',
    description: str = None
) -> None:
    try:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User {tg_id} not found")
            
        current_balance = user.referral_balance or 0
        new_balance = current_balance + amount
        
        # Update user referral balance
        user.referral_balance = new_balance
        
        # Log the balance change
        balance_history = BalanceHistory(
            tg_id=tg_id,
            amount=amount,
            balance_before=current_balance,
            balance_after=new_balance,
            operation_type=operation_type,
            description=description or "Начисление реферального бонуса"
        )
        session.add(balance_history)
        
        await session.commit()
        logger.info(
            f"[DB] Реферальный баланс пользователя {tg_id} обновлён: {current_balance} → {new_balance} "
            f"(описание: {description or 'нет'})"
        )
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при обновлении реферального баланса пользователя {tg_id}: {e}")
        await session.rollback()
        raise


async def check_user_exists(session: AsyncSession, tg_id: int) -> bool:
    stmt = select(exists().where(User.tg_id == tg_id))
    result = await session.execute(stmt)
    return result.scalar()


async def get_balance(session: AsyncSession, tg_id: int) -> float:
    result = await session.execute(select(User.balance).where(User.tg_id == tg_id))
    balance = result.scalar_one_or_none()
    return round(balance, 1) if balance is not None else 0.0


async def get_referral_balance(session: AsyncSession, tg_id: int) -> float:
    result = await session.execute(select(User.referral_balance).where(User.tg_id == tg_id))
    balance = result.scalar_one_or_none()
    return round(balance, 1) if balance is not None else 0.0


async def get_balance_history(
    session: AsyncSession, 
    tg_id: int, 
    limit: int = 10, 
    operation_type: str = None
) -> list:
    """
    Получить историю изменений баланса пользователя
    
    :param session: Асинхронная сессия SQLAlchemy
    :param tg_id: ID пользователя в Telegram
    :param limit: Максимальное количество записей
    :param operation_type: Тип операции (если None, то все типы)
    :return: Список записей истории баланса
    """
    try:
        query = select(BalanceHistory).where(BalanceHistory.tg_id == tg_id)
        
        if operation_type:
            query = query.where(BalanceHistory.operation_type == operation_type)
            
        query = query.order_by(BalanceHistory.created_at.desc()).limit(limit)
        
        result = await session.execute(query)
        history = result.scalars().all()
        
        return [{
            'id': item.id,
            'amount': item.amount,
            'balance_before': item.balance_before,
            'balance_after': item.balance_after,
            'operation_type': item.operation_type,
            'description': item.description,
            'admin_id': item.admin_id,
            'created_at': item.created_at
        } for item in history]
        
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при получении истории баланса пользователя {tg_id}: {e}")
        return []


async def set_user_balance(
    session: AsyncSession, 
    tg_id: int, 
    balance: float,
    operation_type: str = 'manual',
    description: str = None,
    admin_id: int = None
) -> None:
    try:
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"User {tg_id} not found")
            
        current_balance = user.balance or 0
        amount = balance - current_balance
        
        # Update user balance
        user.balance = balance
        
        # Log the balance change if there's an actual change
        if amount != 0:
            balance_history = BalanceHistory(
                tg_id=tg_id,
                amount=amount,
                balance_before=current_balance,
                balance_after=balance,
                operation_type=operation_type,
                description=description or f"Установка баланса в {balance}",
                admin_id=admin_id
            )
            session.add(balance_history)
        
        await session.commit()
        logger.info(
            f"[DB] Баланс пользователя {tg_id} установлен: {balance} "
            f"(изменение: {amount}, операция: {operation_type}, описание: {description or 'нет'})"
        )
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при установке баланса для пользователя {tg_id}: {e}")
        await session.rollback()
        raise


async def update_trial(session: AsyncSession, tg_id: int, status: int):
    try:
        await session.execute(
            update(User).where(User.tg_id == tg_id).values(trial=status)
        )
        await session.commit()
        logger.info(f"[DB] Триал статус обновлён для пользователя {tg_id}: {status}")
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при обновлении триала пользователя {tg_id}: {e}")
        await session.rollback()


async def get_trial(session: AsyncSession, tg_id: int) -> int:
    result = await session.execute(select(User.trial).where(User.tg_id == tg_id))
    trial = result.scalar_one_or_none()
    return trial or 0


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
        else:
            await session.execute(
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
            )
        await session.commit()
        result = await session.execute(select(User).where(User.tg_id == tg_id))
        return dict(result.scalar_one().__dict__)
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при UPSERT пользователя {tg_id}: {e}")
        await session.rollback()
        raise


async def delete_user_data(session: AsyncSession, tg_id: int):
    try:
        await session.execute(delete(Notification).where(Notification.tg_id == tg_id))
        await session.execute(delete(Gift).where(Gift.sender_tg_id == tg_id))
        await session.execute(
            update(Gift)
            .where(Gift.recipient_tg_id == tg_id)
            .values(recipient_tg_id=None)
        )
        await session.execute(delete(Payment).where(Payment.tg_id == tg_id))
        await session.execute(
            delete(Referral).where(
                or_(Referral.referrer_tg_id == tg_id, Referral.referred_tg_id == tg_id)
            )
        )
        await session.execute(delete(CouponUsage).where(CouponUsage.user_id == tg_id))
        await delete_key(session, tg_id)
        await session.execute(delete(TemporaryData).where(TemporaryData.tg_id == tg_id))
        await session.execute(delete(BlockedUser).where(BlockedUser.tg_id == tg_id))
        await session.execute(delete(User).where(User.tg_id == tg_id))
        await session.commit()
        logger.info(f"[DB] Данные пользователя {tg_id} полностью удалены")
    except SQLAlchemyError as e:
        logger.error(f"[DB] Ошибка при удалении данных пользователя {tg_id}: {e}")
        await session.rollback()
        raise


async def mark_trial_extended(tg_id: int, session: AsyncSession):
    await session.execute(update(User).where(User.tg_id == tg_id).values(trial=-1))
    await session.commit()
