from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Union
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import insert, select, update, func, or_, and_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Payment, User, Referral
from logger import logger


class BalanceError(Exception):
    """Base exception for balance-related errors"""
    pass


class InsufficientFundsError(BalanceError):
    """Raised when user doesn't have enough balance"""
    pass


async def add_payment(
    session: AsyncSession,
    tg_id: int,
    amount: float,
    payment_system: str,
    operation_type: str = Payment.TYPE_PAYMENT,
    description: Optional[str] = None,
    admin_id: Optional[int] = None,
) -> Payment:
    """
    Add a new payment/transaction record
    
    Args:
        session: Database session
        tg_id: Telegram user ID
        amount: Transaction amount (can be negative)
        payment_system: Payment system identifier
        operation_type: Type of operation (payment, manual_topup, manual_deduct, etc.)
        description: Optional description of the transaction
        admin_id: ID of the admin who performed the action (if any)
        
    Returns:
        Created Payment object
    """
    try:
        # Create payment record
        payment = Payment(
            tg_id=tg_id,
            amount=float(amount),
            payment_system=payment_system,
            status="success",
            operation_type=operation_type,
            description=description,
            admin_id=admin_id,
            created_at=datetime.utcnow(),
        )
        
        session.add(payment)
        await session.flush()
        
        # Update user balance if this is a balance-affecting operation
        if operation_type in [
            Payment.TYPE_PAYMENT,
            Payment.TYPE_MANUAL_TOPUP,
            Payment.TYPE_MANUAL_DEDUCT,
            Payment.TYPE_REFERRAL_BONUS
        ]:
            await update_user_balance(session, tg_id, float(amount))
        
        await session.commit()
        
        logger.info(
            f"✅ [{operation_type}] {amount:+}₽ for user {tg_id} "
            f"via {payment_system} (ID: {payment.id})"
        )
        
        return payment
        
    except SQLAlchemyError as e:
        await session.rollback()
        logger.error(f"❌ Error adding payment for user {tg_id}: {e}")
        raise


async def update_user_balance(
    session: AsyncSession, tg_id: int, amount: float
) -> float:
    """
    Update user's balance by adding the specified amount
    
    Args:
        session: Database session
        tg_id: Telegram user ID
        amount: Amount to add (can be negative)
        
    Returns:
        New balance
        
    Raises:
        InsufficientFundsError: If balance would go negative
    """
    try:
        # Get current balance
        result = await session.execute(
            select(User.balance).where(User.tg_id == tg_id).with_for_update()
        )
        current_balance = result.scalar_one_or_none()
        
        if current_balance is None:
            raise ValueError(f"User {tg_id} not found")
            
        new_balance = current_balance + amount
        
        # Prevent negative balance for non-admin operations
        if new_balance < 0:
            raise InsufficientFundsError(
                f"Insufficient funds. Current balance: {current_balance:.2f}₽, "
                f"attempted to deduct: {abs(amount):.2f}₽"
            )
        
        # Update balance
        await session.execute(
            update(User)
            .where(User.tg_id == tg_id)
            .values(balance=new_balance)
        )
        
        logger.info(
            f"💰 Updated balance for user {tg_id}: "
            f"{current_balance:.2f}₽ → {new_balance:.2f}₽"
        )
        
        return new_balance
        
    except SQLAlchemyError as e:
        await session.rollback()
        logger.error(f"❌ Error updating balance for user {tg_id}: {e}")
        raise


async def get_user_balance(session: AsyncSession, tg_id: int) -> float:
    """Get current user balance"""
    result = await session.execute(select(User.balance).where(User.tg_id == tg_id))
    return result.scalar() or 0.0


async def get_referral_balance(session: AsyncSession, tg_id: int) -> float:
    """Calculate total referral earnings for a user"""
    result = await session.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(
            Payment.tg_id == tg_id,
            Payment.operation_type == Payment.TYPE_REFERRAL_BONUS,
            Payment.status == "success"
        )
    )
    return float(result.scalar() or 0)


async def get_payment_history(
    session: AsyncSession,
    tg_id: Optional[int] = None,
    operation_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """
    Get paginated payment history with filters
    
    Args:
        session: Database session
        tg_id: Filter by user ID (None for all users)
        operation_type: Filter by operation type
        limit: Number of records per page
        offset: Pagination offset
        start_date: Filter by start date
        end_date: Filter by end date
        
    Returns:
        Tuple of (payments_list, total_count)
    """
    try:
        # Base query
        query = select(Payment)
        count_query = select(func.count(Payment.id))
        
        # Apply filters
        if tg_id is not None:
            query = query.where(Payment.tg_id == tg_id)
            count_query = count_query.where(Payment.tg_id == tg_id)
            
        if operation_type:
            query = query.where(Payment.operation_type == operation_type)
            count_query = count_query.where(Payment.operation_type == operation_type)
            
        if start_date:
            query = query.where(Payment.created_at >= start_date)
            count_query = count_query.where(Payment.created_at >= start_date)
            
        if end_date:
            query = query.where(Payment.created_at <= end_date)
            count_query = count_query.where(Payment.created_at <= end_date)
        
        # Get total count
        total = (await session.execute(count_query)).scalar()
        
        # Get paginated results
        payments = (
            (await session.execute(
                query.order_by(Payment.created_at.desc())
                .limit(limit)
                .offset(offset)
            ))
            .scalars()
            .all()
        )
        
        # Convert to dicts
        payments_list = [
            {
                'id': p.id,
                'tg_id': p.tg_id,
                'amount': p.amount,
                'operation_type': p.operation_type,
                'description': p.description,
                'admin_id': p.admin_id,
                'created_at': p.created_at.isoformat(),
                'status': p.status,
                'payment_system': p.payment_system
            }
            for p in payments
        ]
        
        return payments_list, total
        
    except SQLAlchemyError as e:
        logger.error(f"❌ Error getting payment history: {e}")
        return [], 0


async def get_balance_stats(
    session: AsyncSession,
    tg_id: Optional[int] = None,
    days: int = 30,
) -> Dict[str, Any]:
    """
    Get balance statistics for a user or all users
    
    Args:
        session: Database session
        tg_id: User ID or None for all users
        days: Number of days to look back
        
    Returns:
        Dictionary with balance statistics
    """
    try:
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)
        
        # Base query
        query = select(
            func.sum(
                case(
                    (Payment.amount > 0, Payment.amount),
                    else_=0
                )
            ).label('total_income'),
            func.sum(
                case(
                    (Payment.amount < 0, abs(Payment.amount)),
                    else_=0
                )
            ).label('total_expenses'),
            func.count(Payment.id).label('total_transactions')
        ).where(
            Payment.created_at.between(start_date, end_date),
            Payment.status == 'success'
        )
        
        if tg_id is not None:
            query = query.where(Payment.tg_id == tg_id)
        
        result = (await session.execute(query)).first()
        
        return {
            'total_income': float(result[0] or 0),
            'total_expenses': float(result[1] or 0),
            'total_transactions': result[2] or 0,
            'period_start': start_date.isoformat(),
            'period_end': end_date.isoformat()
        }
        
    except SQLAlchemyError as e:
        logger.error(f"❌ Error getting balance stats: {e}")
        return {
            'total_income': 0,
            'total_expenses': 0,
            'total_transactions': 0,
            'period_start': start_date.isoformat(),
            'period_end': end_date.isoformat()
        }
