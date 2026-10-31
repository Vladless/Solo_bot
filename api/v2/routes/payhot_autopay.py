from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_token
from api.shared.billing_actor import resolve_billing_user_id
from core.settings.payhot_autopay_config import is_payhot_autopay_enabled_for_session
from database import payhot_autopay as dal
from services.payments.payhot.autopay import (
    cancel_subscription,
    delete_method,
    enable_autopay_for_key,
    quote_autopay_for_key,
)
from settings import texts


router = APIRouter(prefix="/payhot-autopay", tags=["PayHotAutopay"])
Session = Annotated[AsyncSession, Depends(get_session)]
Identity = Annotated[Any, Depends(verify_identity_token)]


class EnableRequest(BaseModel):
    method_id: str = Field(..., min_length=1, max_length=64)
    consent: bool = False
    accepted_amount: float = Field(..., gt=0, allow_inf_nan=False)
    accepted_period_days: int = Field(..., ge=1)


@router.get("")
async def status(request: Request, session: Session, identity: Identity):
    """Возвращает привязки и подписки владельца без разрешений кассы."""
    user_id = await resolve_billing_user_id(request, identity, session)
    methods = await dal.list_methods(session, user_id)
    subscriptions = await dal.list_subscriptions(session, user_id)
    processing = {item.subscription_id for item in await dal.list_unfinished_attempts(session, user_id)}
    return {
        "enabled": await is_payhot_autopay_enabled_for_session(session),
        "methods": [
            {
                "id": item.id,
                "label": (
                    texts.PAYHOT_AUTOPAY_SBP_METHOD_LABEL
                    if item.payment_method == "sbp"
                    else texts.PAYHOT_AUTOPAY_CARD_METHOD_LABEL
                ),
                "payment_method": item.payment_method,
                "display_mask": item.display_mask,
                "active": bool(item.is_active),
            }
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
async def quote(client_id: str, request: Request, session: Session, identity: Identity):
    """Показывает согласуемую стоимость автопродления."""
    user_id = await resolve_billing_user_id(request, identity, session)
    if not await is_payhot_autopay_enabled_for_session(session):
        raise HTTPException(status_code=400, detail=texts.PAYHOT_AUTOPAY_DISABLED)
    try:
        result = await quote_autopay_for_key(session, user_id, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {key: result[key] for key in ("amount", "period_days", "tariff_id")}


@router.post("/keys/{client_id}/enable")
async def enable(client_id: str, body: EnableRequest, request: Request, session: Session, identity: Identity):
    """Включает автопродление после согласия с суммой и периодом."""
    user_id = await resolve_billing_user_id(request, identity, session)
    if not await is_payhot_autopay_enabled_for_session(session):
        raise HTTPException(status_code=400, detail=texts.PAYHOT_AUTOPAY_DISABLED)
    if not body.consent:
        raise HTTPException(status_code=400, detail=texts.PAYHOT_AUTOPAY_CONSENT_EXPIRED)
    if await dal.get_owned_method(session, user_id, body.method_id) is None:
        raise HTTPException(status_code=404, detail=texts.PAYHOT_AUTOPAY_ITEM_NOT_FOUND)
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
async def cancel(subscription_id: int, request: Request, session: Session, identity: Identity):
    """Отключает будущие списания по подписке владельца."""
    user_id = await resolve_billing_user_id(request, identity, session)
    try:
        removed = await cancel_subscription(session, user_id, subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=texts.PAYHOT_AUTOPAY_ITEM_NOT_FOUND)
    return {"ok": True}


@router.delete("/methods/{method_id}")
async def remove_method(method_id: str, request: Request, session: Session, identity: Identity):
    """Отзывает сохранённую привязку текущего владельца."""
    user_id = await resolve_billing_user_id(request, identity, session)
    if await dal.get_owned_method(session, user_id, method_id) is None:
        raise HTTPException(status_code=404, detail=texts.PAYHOT_AUTOPAY_ITEM_NOT_FOUND)
    try:
        removed = await delete_method(session, user_id, method_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=texts.PAYHOT_AUTOPAY_ITEM_NOT_FOUND)
    return {"ok": True}
