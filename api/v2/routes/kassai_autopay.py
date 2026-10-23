from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_token
from api.shared.billing_actor import resolve_billing_user_id
from core.settings.kassai_autopay_config import is_kassai_autopay_enabled_for_session
from database import kassai_autopay as dal
from services.payments.kassai_autopay.service import (
    cancel_subscription,
    delete_method,
    enable_autopay_for_key,
    quote_autopay_for_key,
)
from settings import texts


router = APIRouter(prefix="/kassai-autopay", tags=["KassaIAutopay"])
Session = Annotated[AsyncSession, Depends(get_session)]
Identity = Annotated[Any, Depends(verify_identity_token)]


class EnableRequest(BaseModel):
    method_id: str = Field(..., min_length=1, max_length=64)
    consent: bool = False
    accepted_amount: float = Field(..., gt=0, allow_inf_nan=False)
    accepted_period_days: int = Field(..., ge=1)


@router.get("")
async def status(request: Request, session: Session, identity: Identity):
    """Возвращает привязки владельца без платёжных токенов."""
    user_id = await resolve_billing_user_id(request, identity, session)
    methods = await dal.list_methods(session, user_id)
    subscriptions = await dal.list_subscriptions(session, user_id)
    processing = {item.subscription_id for item in await dal.list_unfinished_attempts(session, user_id)}
    return {
        "enabled": await is_kassai_autopay_enabled_for_session(session),
        "methods": [
            {"id": item.id, "label": texts.KASSAI_AUTOPAY_METHOD_LABEL, "active": bool(item.is_active)}
            for item in methods
        ],
        "subscriptions": [
            {
                "id": item.id,
                "client_id": item.client_id,
                "method_id": item.method_id,
                "amount": float(item.amount),
                "period_days": item.period_days,
                "active": bool(item.is_active),
                "processing": item.id in processing,
                "next_payment_date": item.next_payment_date.isoformat() if item.next_payment_date else None,
            }
            for item in subscriptions
        ],
    }


@router.get("/keys/{client_id}/quote")
async def quote(
    client_id: str,
    request: Request,
    session: Session,
    identity: Identity,
):
    """Показывает условия автопродления принадлежащей клиенту подписки."""
    user_id = await resolve_billing_user_id(request, identity, session)
    if not await is_kassai_autopay_enabled_for_session(session):
        raise HTTPException(status_code=400, detail=texts.KASSAI_AUTOPAY_DISABLED)
    try:
        result = await quote_autopay_for_key(session, user_id, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"amount": float(result["amount"]), "period_days": result["period_days"], "tariff_id": result["tariff_id"]}


@router.post("/keys/{client_id}/enable")
async def enable(
    client_id: str,
    body: EnableRequest,
    request: Request,
    session: Session,
    identity: Identity,
):
    """Включает автопродление с явным согласием владельца."""
    user_id = await resolve_billing_user_id(request, identity, session)
    if not await is_kassai_autopay_enabled_for_session(session):
        raise HTTPException(status_code=400, detail=texts.KASSAI_AUTOPAY_DISABLED)
    if not body.consent:
        raise HTTPException(status_code=400, detail=texts.KASSAI_AUTOPAY_CONSENT_EXPIRED)
    if await dal.get_owned_method(session, user_id, body.method_id) is None:
        raise HTTPException(status_code=404, detail=texts.KASSAI_AUTOPAY_ITEM_NOT_FOUND)
    try:
        subscription = await enable_autopay_for_key(
            session,
            user_id,
            client_id,
            method_id=body.method_id,
            consent={
                "accepted": True,
                "source": "web",
                "amount": body.accepted_amount,
                "period_days": body.accepted_period_days,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": subscription.id, "amount": float(subscription.amount)}


@router.delete("/subscriptions/{subscription_id}")
async def cancel(
    subscription_id: int,
    request: Request,
    session: Session,
    identity: Identity,
):
    """Отключает подписку владельца даже при выключенной кассе."""
    user_id = await resolve_billing_user_id(request, identity, session)
    owned = next((item for item in await dal.list_subscriptions(session, user_id) if item.id == subscription_id), None)
    if owned is None:
        raise HTTPException(status_code=404, detail=texts.KASSAI_AUTOPAY_ITEM_NOT_FOUND)
    try:
        removed = await cancel_subscription(session, user_id, subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=texts.KASSAI_AUTOPAY_ITEM_NOT_FOUND)
    return {"ok": True}


@router.delete("/methods/{method_id}")
async def remove_method(
    method_id: str,
    request: Request,
    session: Session,
    identity: Identity,
):
    """Отзывает принадлежащую владельцу привязку счёта СБП."""
    user_id = await resolve_billing_user_id(request, identity, session)
    if await dal.get_owned_method(session, user_id, method_id) is None:
        raise HTTPException(status_code=404, detail=texts.KASSAI_AUTOPAY_ITEM_NOT_FOUND)
    try:
        removed = await delete_method(session, user_id, method_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=texts.KASSAI_AUTOPAY_ITEM_NOT_FOUND)
    return {"ok": True}
