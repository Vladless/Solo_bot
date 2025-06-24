import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Union

from aiogram import F, Router, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import BOT_USERNAME
from database.models import Payment, User
from database.payments import (
    add_payment,
    get_payment_history,
    get_user_balance,
    get_referral_balance,
    update_user_balance,
    InsufficientFundsError,
    BalanceError,
)
from handlers.admin.panel.keyboard import (
    build_admin_back_btn,
    build_admin_back_kb,
    build_admin_menu_kb,
)
from logger import logger

router = Router()

# Constants
BALANCE_ACTIONS_PER_PAGE = 10

# States
class BalanceStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_reason = State()
    viewing_history = State()


# Callback data classes
class BalanceActionCallback(types.CallbackData, prefix="balance"):
    action: str
    user_id: int
    amount: Optional[float] = None
    page: int = 1


# Helper functions
async def get_balance_keyboard(user_id: int, current_page: int = 1) -> InlineKeyboardMarkup:
    """Create keyboard for balance management"""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        types.InlineKeyboardButton(
            text="➕ Пополнить",
            callback_data=BalanceActionCallback(
                action="topup", user_id=user_id
            ).pack(),
        ),
        types.InlineKeyboardButton(
            text="➖ Списать",
            callback_data=BalanceActionCallback(
                action="deduct", user_id=user_id
            ).pack(),
        ),
    )
    
    builder.row(
        types.InlineKeyboardButton(
            text="📋 История операций",
            callback_data=BalanceActionCallback(
                action="history", user_id=user_id, page=current_page
            ).pack(),
        )
    )
    
    builder.row(
        types.InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"admin_user_editor:{user_id}",
        )
    )
    
    return builder.as_markup()


def format_balance_history(transactions: list, page: int, total: int) -> str:
    """Format transaction history for display"""
    if not transactions:
        return "История операций пуста."
    
    # Calculate total pages
    total_pages = (total + BALANCE_ACTIONS_PER_PAGE - 1) // BALANCE_ACTIONS_PER_PAGE
    
    # Format transactions
    lines = []
    for t in transactions:
        amount = f"+{t['amount']:.2f}₽" if t['amount'] >= 0 else f"{t['amount']:.2f}₽"
        date = datetime.fromisoformat(t['created_at']).strftime("%d.%m.%Y %H:%M")
        desc = f" - {t['description']}" if t['description'] else ""
        
        lines.append(
            f"<b>{date}</b> | {amount} | {t['operation_type']}{desc}"
        )
    
    # Add header and pagination
    header = f"📋 <b>История операций</b> (стр. {page}/{max(1, total_pages)})\n\n"
    pagination = f"\n\nСтраница {page} из {max(1, total_pages)}"
    
    return header + "\n".join(lines) + pagination


# Command handlers
@router.callback_query(BalanceActionCallback.filter(F.action == "menu"))
async def handle_balance_menu(
    callback: CallbackQuery,
    callback_data: BalanceActionCallback,
    session: AsyncSession,
):
    """Show balance management menu"""
    user_id = callback_data.user_id
    
    # Get user data
    user = await session.get(User, user_id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    
    # Get balances
    balance = await get_user_balance(session, user_id)
    ref_balance = await get_referral_balance(session, user_id)
    
    # Format message
    text = (
        f"👤 <b>Управление балансом</b>\n"
        f"Пользователь: <code>{user_id}</code>\n"
        f"Имя: {user.first_name or 'Не указано'}\n"
        f"Username: @{user.username or 'Нет'}\n\n"
        f"💰 <b>Основной баланс:</b> {balance:.2f}₽\n"
        f"🎁 <b>Реферальный баланс:</b> {ref_balance:.2f}₽"
    )
    
    # Send or update message
    keyboard = await get_balance_keyboard(user_id)
    
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest:
        await callback.answer()
        await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(BalanceActionCallback.filter(F.action.in_(["topup", "deduct"])))
async def handle_balance_change_prompt(
    callback: CallbackQuery,
    callback_data: BalanceActionCallback,
    state: FSMContext,
):
    """Prompt for amount to add/deduct"""
    action = callback_data.action
    user_id = callback_data.user_id
    
    action_text = "пополнить" if action == "topup" else "списать"
    
    # Set state
    await state.update_data(
        action=action,
        user_id=user_id,
    )
    
    # Ask for amount
    await callback.message.edit_text(
        f"💳 Введите сумму для {action_text} (только цифры, можно с копейками):",
        reply_markup=build_admin_back_btn(f"balance:menu:{user_id}"),
    )
    
    # Set next state
    await state.set_state(BalanceStates.waiting_for_amount)
    await callback.answer()


@router.message(BalanceStates.waiting_for_amount)
async def handle_balance_amount_input(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Handle amount input for balance change"""
    try:
        # Parse amount
        amount = float(message.text.replace(",", "."))
        if amount <= 0:
            raise ValueError("Amount must be positive")
            
        # Get state data
        data = await state.get_data()
        action = data["action"]
        user_id = data["user_id"]
        
        # For deductions, check if user has enough balance
        if action == "deduct":
            current_balance = await get_user_balance(session, user_id)
            if amount > current_balance:
                await message.answer(
                    f"❌ Недостаточно средств. Текущий баланс: {current_balance:.2f}₽"
                )
                return
        
        # Update state and ask for reason
        await state.update_data(amount=amount)
        
        action_text = "пополнения" if action == "topup" else "списания"
        
        await message.answer(
            f"📝 Введите причину {action_text} (необязательно):",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        
        await state.set_state(BalanceStates.waiting_for_reason)
        
    except (ValueError, TypeError):
        await message.answer("❌ Неверный формат суммы. Введите число, например: 100 или 50.5")


@router.message(BalanceStates.waiting_for_reason)
async def handle_balance_reason_input(
    message: Message, state: FSMContext, session: AsyncSession
):
    """Handle reason input and apply balance change"""
    reason = message.text or "Без указания причины"
    data = await state.get_data()
    
    action = data["action"]
    user_id = data["user_id"]
    amount = data["amount"]
    
    # Determine operation type and amount sign
    if action == "topup":
        operation_type = Payment.TYPE_MANUAL_TOPUP
        amount_abs = amount
        action_text = "пополнен"
    else:  # deduct
        operation_type = Payment.TYPE_MANUAL_DEDUCT
        amount_abs = -amount
        action_text = "списан"
    
    try:
        # Add payment record and update balance
        payment = await add_payment(
            session=session,
            tg_id=user_id,
            amount=amount_abs,
            payment_system="manual",
            operation_type=operation_type,
            description=reason,
            admin_id=message.from_user.id,
        )
        
        # Get updated balance
        new_balance = await get_user_balance(session, user_id)
        
        # Send success message
        text = (
            f"✅ Баланс успешно {action_text} на {amount:.2f}₽\n"
            f"💬 Причина: {reason}\n\n"
            f"💰 Новый баланс: {new_balance:.2f}₽"
        )
        
        await message.answer(
            text,
            reply_markup=await get_balance_keyboard(user_id),
        )
        
    except Exception as e:
        logger.error(f"Error updating balance: {e}")
        await message.answer(
            f"❌ Ошибка при обновлении баланса: {str(e)}",
            reply_markup=await get_balance_keyboard(user_id),
        )
    
    # Clear state
    await state.clear()


@router.callback_query(BalanceActionCallback.filter(F.action == "history"))
async def handle_balance_history(
    callback: CallbackQuery,
    callback_data: BalanceActionCallback,
    session: AsyncSession,
):
    """Show transaction history"""
    user_id = callback_data.user_id
    page = callback_data.page or 1
    
    # Get paginated history
    transactions, total = await get_payment_history(
        session=session,
        tg_id=user_id,
        limit=BALANCE_ACTIONS_PER_PAGE,
        offset=(page - 1) * BALANCE_ACTIONS_PER_PAGE,
    )
    
    # Format message
    text = format_balance_history(transactions, page, total)
    
    # Build pagination keyboard
    total_pages = (total + BALANCE_ACTIONS_PER_PAGE - 1) // BALANCE_ACTIONS_PER_PAGE
    
    builder = InlineKeyboardBuilder()
    
    # Add pagination buttons if needed
    if total_pages > 1:
        if page > 1:
            builder.add(
                types.InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=BalanceActionCallback(
                        action="history",
                        user_id=user_id,
                        page=page - 1,
                    ).pack(),
                )
            )
        
        if page < total_pages:
            builder.add(
                types.InlineKeyboardButton(
                    text="Вперед ➡️",
                    callback_data=BalanceActionCallback(
                        action="history",
                        user_id=user_id,
                        page=page + 1,
                    ).pack(),
                )
            )
    
    # Add back button
    builder.row(
        types.InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=BalanceActionCallback(
                action="menu", user_id=user_id
            ).pack(),
        )
    )
    
    # Send or update message
    try:
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
        )
    except TelegramBadRequest:
        await callback.answer()
    
    await callback.answer()


# Register handlers
def register_handlers(dp):
    dp.include_router(router)
