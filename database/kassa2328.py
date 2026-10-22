import copy
import json

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings import kassa2328_config as config
from database.access.resolution import UserId, resolve_user_optional
from database.db import async_session_maker
from database.models import Key, Payment, User
from database.payments import MOSCOW_TZ, add_payment, lock_payment_for_processing
from services.payments.kassa2328.api import PaymentError, encode_json, positive_decimal


PAYMENT_SYSTEM = "2328"
SUCCESS_STATUSES = {"paid", "overpaid"}
REFUND_STATUSES = {"refund_process", "refund_fail", "refund_paid"}
PAYMENT_STATUSES = {
    "pending",
    "check",
    "awaiting_confirmation",
    "paid",
    "overpaid",
    "underpaid_check",
    "underpaid",
    "cancel",
    "aml_lock",
    *REFUND_STATUSES,
}


def _order(row: Payment) -> dict | None:
    """Представляет сохранённый счёт текущего проекта 2328."""
    meta = row.metadata_ or {}
    request = meta.get("kassa2328_request")
    if (
        row.payment_system != PAYMENT_SYSTEM
        or meta.get("kassa2328_project") != config.KASSA2328_PROJECT_UUID
        or not isinstance(request, dict)
        or request.get("order_id") != row.payment_id
    ):
        return None
    return {
        "order_id": row.payment_id,
        "payment_uuid": meta.get("kassa2328_uuid"),
        "user_id": row.user_id,
        "amount_rub": positive_decimal(row.amount),
        "request_json": encode_json(request).decode("utf-8"),
        "response": copy.deepcopy(meta.get("kassa2328_response")),
        "credited": row.status == "success",
        "status": row.status,
        "provider_status": meta.get("kassa2328_status"),
        "metadata": copy.deepcopy(meta),
    }


async def lock_checkout_owner(session: AsyncSession, user_ref: int) -> UserId:
    """Блокирует канонического владельца создаваемого счёта."""
    user = await resolve_user_optional(session, user_ref)
    if user is None:
        raise PaymentError("Пользователь не найден")
    owner_id = await session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    if owner_id is None:
        raise PaymentError("Пользователь удалён")
    return UserId(owner_id)


async def get_checkout_key(
    session: AsyncSession,
    owner: UserId,
    *,
    client_id: str | None = None,
    email: str | None = None,
) -> Key | None:
    """Блокирует подписку владельца для фиксации условий оплаты."""
    if not client_id and not email:
        return None
    query = select(Key).where(Key.user_id == int(owner))
    if client_id:
        query = query.where(Key.client_id == client_id)
    if email:
        query = query.where(Key.email == email)
    return await session.scalar(query.with_for_update().execution_options(populate_existing=True))


async def find_checkout_order(
    session: AsyncSession, owner: UserId, fingerprint: str, *, order_id: str | None = None
) -> dict | None:
    """Находит предыдущую попытку без повторного создания счёта."""
    if order_id:
        row = await session.scalar(select(Payment).where(Payment.payment_id == order_id))
        if row is not None:
            order = _order(row)
            if order is None or row.user_id != int(owner):
                raise PaymentError("Счёт не принадлежит пользователю")
            if (row.metadata_ or {}).get("kassa2328_fingerprint") != fingerprint:
                raise PaymentError("Условия сохранённого платежа изменились; выберите сумму заново")
            return order
    cutoff = datetime.now(MOSCOW_TZ).replace(tzinfo=None) - timedelta(days=1)
    rows = (
        await session.scalars(
            select(Payment)
            .where(
                Payment.user_id == int(owner),
                Payment.payment_system == PAYMENT_SYSTEM,
                Payment.status == "pending",
                Payment.created_at >= cutoff,
                Payment.metadata_["kassa2328_fingerprint"].as_string() == fingerprint,
            )
            .order_by(Payment.id.desc())
            .limit(10)
        )
    ).all()
    for row in rows:
        order = _order(row)
        if order and order["provider_status"] not in {"cancel", "underpaid", *REFUND_STATUSES}:
            return order
    return None


async def save_order(session: AsyncSession, owner: UserId, amount: Decimal, payload: dict, metadata: dict) -> dict:
    """Сохраняет владельца и неизменяемые условия до запроса к кассе."""
    internal_id = await add_payment(
        session,
        user_id=int(owner),
        amount=float(amount),
        payment_system=PAYMENT_SYSTEM,
        status="pending",
        currency="RUB",
        payment_id=payload["order_id"],
        metadata=metadata,
        original_amount=float(positive_decimal(payload["amount"])) if payload["currency"] != "RUB" else None,
    )
    row = await session.scalar(select(Payment).where(Payment.id == internal_id))
    order = _order(row) if row is not None else None
    if order is None:
        raise PaymentError("Не удалось сохранить счёт 2328")
    return order


async def load_order(order_id: str, user_ref: int | None = None) -> dict | None:
    """Загружает счёт с проверкой его владельца."""
    async with async_session_maker() as session:
        row = await session.scalar(select(Payment).where(Payment.payment_id == order_id))
        if row is None:
            return None
        if user_ref is not None:
            user = await resolve_user_optional(session, user_ref)
            if user is None or row.user_id != user.id:
                return None
        return _order(row)


def validate_payment(order: dict, data: dict) -> None:
    """Сверяет реквизиты уведомления с исходным счётом."""
    expected = json.loads(order["request_json"])
    payment_uuid = data.get("uuid")
    if not isinstance(payment_uuid, str) or not 1 <= len(payment_uuid) <= 64:
        raise PaymentError("В ответе 2328 отсутствует UUID")
    if data.get("order_id") != order["order_id"]:
        raise PaymentError("Ответ 2328 относится к другому заказу")
    if order["payment_uuid"] and order["payment_uuid"] != payment_uuid:
        raise PaymentError("UUID платежа 2328 не совпадает")
    if (
        positive_decimal(data.get("amount")) != positive_decimal(expected["amount"])
        or data.get("currency") != expected["currency"]
    ):
        raise PaymentError("Сумма или валюта счёта 2328 не совпадает")
    if data.get("payment_status") not in PAYMENT_STATUSES:
        raise PaymentError("Неизвестный статус платежа 2328")


async def save_response(order_id: str, data: dict) -> dict:
    """Сохраняет подтверждённый ответ без отката окончательного статуса."""
    async with async_session_maker() as session:
        row = await lock_payment_for_processing(session, order_id)
        order = _order(row) if row is not None else None
        if order is None:
            raise PaymentError("Счёт 2328 не найден")
        validate_payment(order, data)
        meta = copy.deepcopy(row.metadata_ or {})
        previous = meta.get("kassa2328_status")
        status = data["payment_status"]
        terminal = previous in {"refund_paid", "cancel", "underpaid", *SUCCESS_STATUSES}
        regresses = (
            previous == "refund_paid"
            or (previous in REFUND_STATUSES and status not in REFUND_STATUSES)
            or (
                (row.status == "success" or previous in SUCCESS_STATUSES)
                and status not in REFUND_STATUSES | SUCCESS_STATUSES
            )
            or (terminal and status in {"pending", "check", "awaiting_confirmation", "underpaid_check"})
        )
        meta["kassa2328_uuid"] = data["uuid"]
        if not regresses:
            meta["kassa2328_status"] = status
            meta["kassa2328_response"] = {key: value for key, value in data.items() if key != "sign"}
        if status in {"underpaid", "aml_lock", "refund_paid"}:
            meta["kassa2328_review_required"] = status
        row.metadata_ = meta
        await session.commit()
        return _order(row)


async def save_checkout_result(session: AsyncSession, row: Payment, result: dict) -> None:
    """Записывает результат выполнения оплаченной операции."""
    metadata = copy.deepcopy(row.metadata_ or {})
    metadata["kassa2328_checkout_result"] = result
    row.metadata_ = metadata
    await session.flush()
