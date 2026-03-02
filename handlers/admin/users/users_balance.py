from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_balance, set_user_balance, update_balance
from utils.csv_export import export_user_all_payments_csv
from database.models import Payment
from database.payments import add_payment
from filters.admin import IsAdminFilter

from .keyboard import (
    AdminUserEditorCallback,
    build_users_balance_change_kb,
    build_users_balance_kb,
)
from .users_states import UserEditorState


router = Router()


def format_admin_operation(amount: float, created_at: datetime) -> str:
    date_str = created_at.strftime("%Y-%m-%d %H:%M:%S")
    sign = "+" if amount > 0 else "-" if amount < 0 else ""
    abs_amount = abs(amount)
    return f"\n<blockquote>Админ {sign}{abs_amount}Р\n⏳ Дата: {date_str}</blockquote>"


def format_user_payment(
    amount: float, created_at: datetime, payment_system: str, status: str, payment_id: str | None = None,
) -> str:
    date_str = created_at.strftime("%Y-%m-%d %H:%M:%S")
    abs_amount = abs(amount)
    system_name = payment_system or "Неизвестно"
    pid = f"<code>{payment_id}</code>" if payment_id else "—"
    return (
        f"\n<blockquote>💸 Сумма: {abs_amount} | {system_name}\n"
        f"📌 Статус: {status}\n"
        f"🆔 ID: {pid}\n"
        f"⏳ Дата: {date_str}</blockquote>"
    )


async def _render_balance_page(
    callback_query: CallbackQuery,
    session: AsyncSession,
    tg_id: int,
    page: int = 0,
):
    balance = await get_balance(session, tg_id)
    balance = int(balance or 0)

    total_count_result = await session.execute(
        select(func.count()).where(Payment.tg_id == tg_id)
    )
    total = total_count_result.scalar() or 0

    total_pages = max(1, (total + 4) // 5)
    page = max(0, min(page, total_pages - 1))

    stmt = (
        select(
            Payment.amount,
            Payment.created_at,
            Payment.payment_system,
            Payment.status,
            Payment.payment_id,
        )
        .where(Payment.tg_id == tg_id)
        .order_by(Payment.created_at.desc())
        .offset(page * 5)
        .limit(5)
    )
    result = await session.execute(stmt)
    records = result.all()

    text = f"<b>💵 Изменение баланса</b>\n\n🆔 ID: <b>{tg_id}</b>\n💰 Баланс: <b>{balance}Р</b>"
    text += f"\n\n<b>📊 Все операции ({total}), стр. {page + 1}/{total_pages}:</b>"

    if records:
        for amount, created_at, payment_system, status, payment_id in records:
            text += format_user_payment(amount, created_at, payment_system, status, payment_id)
    else:
        text += "\n<i>🚫 Операции отсутствуют</i>"

    kb = await build_users_balance_kb(session, tg_id, page=page, total_pages=total_pages)
    await callback_query.message.edit_text(text=text, reply_markup=kb)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_balance_edit"),
    IsAdminFilter(),
)
async def handle_balance_change(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    page = int(callback_data.data) if callback_data.data is not None else 0
    await _render_balance_page(callback_query, session, callback_data.tg_id, page)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_balance_page"),
    IsAdminFilter(),
)
async def handle_balance_page(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    page = int(callback_data.data) if callback_data.data is not None else 0
    await _render_balance_page(callback_query, session, callback_data.tg_id, page)


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_balance_export"),
    IsAdminFilter(),
)
async def handle_balance_export(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    csv_file = await export_user_all_payments_csv(tg_id=tg_id, session=session)
    await callback_query.message.answer_document(csv_file)
    await callback_query.answer()


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_balance_add"),
    IsAdminFilter(),
)
async def handle_balance_add(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
    session: AsyncSession,
):
    tg_id = callback_data.tg_id
    amount = callback_data.data

    if amount is not None:
        amount = int(amount)
        old_balance = await get_balance(session, tg_id)

        if amount >= 0:
            await update_balance(session, tg_id, amount)
            new_balance = old_balance + amount
            if amount != 0:
                await add_payment(
                    session=session,
                    tg_id=tg_id,
                    amount=amount,
                    payment_system="admin",
                    status="success",
                )
        else:
            new_balance = max(0, old_balance + amount)
            await set_user_balance(session, tg_id, new_balance)
            deducted = old_balance - new_balance
            if deducted > 0:
                await add_payment(
                    session=session,
                    tg_id=tg_id,
                    amount=-deducted,
                    payment_system="admin",
                    status="success",
                )

        if old_balance != new_balance:
            await handle_balance_change(callback_query, callback_data, session)
        return

    await state.update_data(tg_id=tg_id, op_type="add")
    await state.set_state(UserEditorState.waiting_for_balance)

    await callback_query.message.edit_text(
        text="✍️ Введите сумму, которую хотите добавить на баланс пользователя:",
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_balance_take"),
    IsAdminFilter(),
)
async def handle_balance_take(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
):
    tg_id = callback_data.tg_id

    await state.update_data(tg_id=tg_id, op_type="take")
    await state.set_state(UserEditorState.waiting_for_balance)

    await callback_query.message.edit_text(
        text="✍️ Введите сумму, которую хотите вычесть из баланса пользователя:",
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.callback_query(
    AdminUserEditorCallback.filter(F.action == "users_balance_set"),
    IsAdminFilter(),
)
async def handle_balance_set(
    callback_query: CallbackQuery,
    callback_data: AdminUserEditorCallback,
    state: FSMContext,
):
    tg_id = callback_data.tg_id

    await state.update_data(tg_id=tg_id, op_type="set")
    await state.set_state(UserEditorState.waiting_for_balance)

    await callback_query.message.edit_text(
        text="✍️ Введите баланс, который хотите установить пользователю:",
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.message(UserEditorState.waiting_for_balance, IsAdminFilter())
async def handle_balance_input(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    op_type = data.get("op_type")

    if not message.text.isdigit() or int(message.text) < 0:
        await message.answer(
            text="🚫 Пожалуйста, введите корректную сумму!",
            reply_markup=build_users_balance_change_kb(tg_id),
        )
        return

    amount = int(message.text)

    if op_type == "add":
        text = f"✅ К балансу пользователя добавлено <b>{amount}Р</b>"
        await update_balance(session, tg_id, amount)
        if amount != 0:
            await add_payment(
                session=session,
                tg_id=tg_id,
                amount=amount,
                payment_system="admin",
                status="success",
            )
    elif op_type == "take":
        current_balance = await get_balance(session, tg_id)
        new_balance = max(0, current_balance - amount)
        deducted = current_balance if amount > current_balance else amount
        text = f"✅ Из баланса пользователя было вычтено <b>{deducted}Р</b>"
        await set_user_balance(session, tg_id, new_balance)
        if deducted > 0:
            await add_payment(
                session=session,
                tg_id=tg_id,
                amount=-deducted,
                payment_system="admin",
                status="success",
            )
    else:
        current_balance = await get_balance(session, tg_id)
        text = f"✅ Баланс пользователя изменён на <b>{amount}Р</b>"
        await set_user_balance(session, tg_id, amount)
        delta = amount - current_balance
        if delta != 0:
            await add_payment(
                session=session,
                tg_id=tg_id,
                amount=delta,
                payment_system="admin",
                status="success",
            )

    await message.answer(text=text, reply_markup=build_users_balance_change_kb(tg_id))
