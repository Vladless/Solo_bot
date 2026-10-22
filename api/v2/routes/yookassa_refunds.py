from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.admin_permissions import require_full_admin
from api.depends import get_session, verify_identity_admin
from api.shared.billing_actor import resolve_billing_user_id
from services.payments.yookassa import refunds


router = APIRouter(prefix="/admin/yookassa/refunds", tags=["YooKassaRefunds"])
ADMIN_IDENTITY = Depends(verify_identity_admin)
DATABASE_SESSION = Depends(get_session)
PAYMENT_RECORD_ID = Path(..., gt=0)


class RefundPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: str | None = Field(None, strict=True, max_length=32)
    description: str = Field("", strict=True, max_length=250)
    receipt_email: str | None = Field(None, strict=True, max_length=254)


async def _authorize(session: AsyncSession, identity) -> None:
    """Проверяет актуальные полные права администратора."""
    await require_full_admin(session, identity)


async def _actor(request: Request, session: AsyncSession, identity) -> int:
    """Помечает веб-администратора каноническим номером клиента."""
    await _authorize(session, identity)
    user_id = int(await resolve_billing_user_id(request, identity, session))
    if user_id <= 0:
        raise HTTPException(status_code=403, detail="Forbidden")
    await session.commit()
    return -user_id


async def _call(operation, *args: object, **kwargs: object):
    """Возвращает безопасную ошибку операции возврата."""
    try:
        return await operation(*args, **kwargs)
    except refunds.RefundError as exc:
        code = getattr(exc, "code", "invalid_request")
        status = {
            "not_found": 404,
            "forbidden": 403,
            "unavailable": 503,
            "expired": 409,
            "not_refundable": 409,
            "amount_exceeded": 409,
            "operation_pending": 409,
            "terms_changed": 409,
            "manual_review": 409,
        }.get(code, 400)
        raise HTTPException(status_code=status, detail={"message": str(exc), "code": code}) from None


@router.get("/payments")
async def list_payments(
    search: str = Query("", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Показывает платежи ЮKassa для оформления возврата."""
    await _authorize(session, identity)
    await session.commit()
    return await _call(refunds.list_payments, search=search.strip(), offset=offset, limit=limit)


@router.get("/payments/{payment_record_id}")
async def get_payment(
    payment_record_id: int = PAYMENT_RECORD_ID,
    refresh: bool = False,
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Показывает платёж и подтверждённые возвраты."""
    await _authorize(session, identity)
    await session.commit()
    return await _call(refunds.get_payment, payment_record_id, refresh=refresh)


@router.post("/payments/{payment_record_id}/preview")
async def preview_refund(
    body: RefundPreviewRequest,
    request: Request,
    payment_record_id: int = PAYMENT_RECORD_ID,
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Сохраняет расчёт возврата для отдельного подтверждения."""
    actor_id = await _actor(request, session, identity)
    return await _call(
        refunds.preview_refund,
        payment_record_id,
        amount=body.amount,
        description=body.description.strip(),
        receipt_email=body.receipt_email,
        actor_id=actor_id,
    )


@router.get("/payments/{payment_record_id}/history")
async def list_refunds(
    payment_record_id: int = PAYMENT_RECORD_ID,
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Возвращает историю операций выбранного платежа."""
    await _authorize(session, identity)
    await session.commit()
    return {"items": await _call(refunds.list_refunds, payment_record_id)}


@router.post("/operations/{operation_id}/confirm")
async def confirm_refund(
    operation_id: UUID,
    request: Request,
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Подтверждает сохранённый возврат от имени его администратора."""
    actor_id = await _actor(request, session, identity)
    return await _call(refunds.execute_refund, str(operation_id), actor_id=actor_id)


@router.get("/operations/{operation_id}")
async def get_refund(
    operation_id: UUID,
    refresh: bool = False,
    identity=ADMIN_IDENTITY,
    session: AsyncSession = DATABASE_SESSION,
):
    """Показывает статус сохранённого возврата."""
    await _authorize(session, identity)
    await session.commit()
    return await _call(refunds.get_refund, str(operation_id), refresh=refresh)
