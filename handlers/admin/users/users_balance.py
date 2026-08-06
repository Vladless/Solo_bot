from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_balance, set_user_balance, update_balance
from database.access.resolution import resolve_user_optional
from database.payments import add_payment, count_balance_activity, get_balance_activity
from filters.admin import IsAdminFilter
from utils.csv_export import export_user_all_payments_csv

from ..panel.headers import card, menu_text, quote, section
from .keyboard import (
    AdminUserEditorCallback,
    build_users_balance_change_kb,
    build_users_balance_kb,
)
from .users_states import UserEditorState


router = Router()


def format_user_payment(
    amount: float,
    created_at: datetime,
    payment_system: str,
    status: str,
    payment_id: str | None = None,
) -> str:
    """Возвращает секцию платежа для истории баланса."""
    rows = [
        f"Способ: {payment_system or 'неизвестно'}",
        f"Статус: {status}",
        f"Дата: {created_at.strftime('%d.%m.%y %H:%M')}",
    ]
    if payment_id:
        rows.append(f"ID: {payment_id}")
    return section(f"💸 Пополнение {abs(amount)} ₽", *rows)


def format_gift_purchase(amount: float, created_at: datetime) -> str:
    """Возвращает секцию покупки подарка для истории баланса."""
    return section(
        f"🎁 Подарок −{abs(int(amount or 0))} ₽",
        f"Дата: {created_at.strftime('%d.%m.%y %H:%M')}",
    )


async def _render_balance_page(
    callback_query: CallbackQuery,
    session: AsyncSession,
    tg_id: int,
    page: int = 0,
):
    balance = await get_balance(session, tg_id)
    balance = int(balance or 0)

    u = await resolve_user_optional(session, tg_id)
    uid = u.id if u is not None else None
    tg_ref = u.tg_id if u is not None else tg_id

    total = await count_balance_activity(session, uid=uid, tg_id=tg_ref)

    total_pages = max(1, (total + 4) // 5)
    page = max(0, min(page, total_pages - 1))

    records = await get_balance_activity(session, uid=uid, tg_id=tg_ref, limit=5, offset=page * 5)

    history = card(*[
        format_gift_purchase(row.amount, row.created_at)
        if row.kind == "gift"
        else format_user_payment(row.amount, row.created_at, row.system, row.status, row.ref)
        for row in records
    ])

    text = menu_text(
        "Баланс",
        f"Клиент <code>{tg_id}</code>",
        quote(f"Баланс: {balance} ₽\nОпераций: {total}\nСтраница: {page + 1}/{total_pages}"),
        history or quote("Операций пока нет"),
    )

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
        text=menu_text(
            "Баланс",
            "✍️ Сколько добавить на баланс?",
            markup=build_users_balance_change_kb(tg_id),
        ),
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
        text=menu_text(
            "Баланс",
            "✍️ Сколько списать с баланса?",
            markup=build_users_balance_change_kb(tg_id),
        ),
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
        text=menu_text(
            "Баланс",
            "✍️ Новый баланс клиента.",
            markup=build_users_balance_change_kb(tg_id),
        ),
        reply_markup=build_users_balance_change_kb(tg_id),
    )


@router.message(UserEditorState.waiting_for_balance, IsAdminFilter())
async def handle_balance_input(message: Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    tg_id = data.get("tg_id")
    op_type = data.get("op_type")

    if not message.text.isdigit() or int(message.text) < 0:
        await message.answer(
            text=menu_text("Баланс", "❌ Нужна сумма числом.", markup=build_users_balance_change_kb(tg_id)),
            reply_markup=build_users_balance_change_kb(tg_id),
        )
        return

    amount = int(message.text)

    if op_type == "add":
        text = menu_text("Баланс", f"✅ Баланс пополнен на <b>{amount}Р</b>")
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
        text = menu_text("Баланс", f"✅ С баланса списано <b>{deducted}Р</b>")
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
        text = menu_text("Баланс", f"✅ Баланс теперь <b>{amount}Р</b>")
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

    await state.clear()
    await message.answer(text=text, reply_markup=build_users_balance_change_kb(tg_id))
