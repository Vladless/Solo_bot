from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin, verify_identity_token
from api.shared.billing_actor import resolve_billing_user_id
from core.settings.yookassa_autopay_config import is_yookassa_autopay_enabled_for_session
from database import yookassa_autopay as dal
from services.payments.yookassa_autopay.service import (
    enable_autopay_for_key,
    quote_autopay_for_key,
    resolve_unknown_attempt,
)
from settings import texts


router = APIRouter(prefix="/yookassa-autopay", tags=["YooKassaAutopay"])


class EnableRequest(BaseModel):
    card_id: str = Field(..., min_length=1, max_length=128)
    consent: bool = False
    accepted_amount: float = Field(..., gt=0)
    accepted_gross_amount: float | None = Field(None, gt=0)
    accepted_period_days: int = Field(..., ge=1)


class ResolveRequest(BaseModel):
    payment_id: str = Field(..., min_length=1, max_length=128)


@router.get("")
async def status(
    request: Request, session: AsyncSession = Depends(get_session), identity=Depends(verify_identity_token)
):
    uid = await resolve_billing_user_id(request, identity, session)
    cards = await dal.list_cards(session, uid)
    subscriptions = await dal.list_subscriptions(session, uid)
    pending = await dal.get_legacy_pending(session, user_id=uid)
    return {
        "enabled": await is_yookassa_autopay_enabled_for_session(session),
        "cards": [
            {
                "id": card.id,
                "type": card.card_type,
                "mask": card.card_mask,
                "bank_name": card.bank_name,
                "active": bool(card.is_active),
            }
            for card in cards
        ],
        "subscriptions": [
            {
                "id": sub.id,
                "client_id": sub.client_id,
                "amount": sub.amount,
                "gross_amount": float(sub.accepted_gross_amount)
                if sub.accepted_gross_amount is not None
                else sub.amount,
                "period_days": sub.period_days,
                "active": bool(sub.is_active),
                "processing": bool(sub.is_processing),
                "next_payment_date": sub.next_payment_date.isoformat() if sub.next_payment_date else None,
            }
            for sub in subscriptions
        ],
        "pending_payments": [
            {"id": item.payment_id, "confirmation_url": item.confirmation_url, "amount": item.amount}
            for item in pending
            if item.confirmation_url
        ],
    }


@router.get("/keys/{client_id}/quote")
async def quote(
    client_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    uid = await resolve_billing_user_id(request, identity, session)
    try:
        result = await quote_autopay_for_key(session, uid, client_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "amount": result["amount"],
        "gross_amount": result["gross_amount"],
        "amount_quote": result["amount_quote"],
        "tariff_id": result["tariff_id"],
        "period_days": result["period_days"],
    }


@router.post("/keys/{client_id}/enable")
async def enable(
    client_id: str,
    body: EnableRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    uid = await resolve_billing_user_id(request, identity, session)
    try:
        sub = await enable_autopay_for_key(
            session,
            uid,
            client_id,
            body.card_id,
            consent={
                "accepted": body.consent,
                "amount": body.accepted_amount,
                "gross_amount": body.accepted_gross_amount,
                "period_days": body.accepted_period_days,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "id": sub.id, "amount": sub.amount}


@router.delete("/subscriptions/{subscription_id}")
async def cancel(
    subscription_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    uid = await resolve_billing_user_id(request, identity, session)
    if not await dal.cancel_subscription(session, uid, subscription_id):
        raise HTTPException(status_code=404, detail=texts.YOOKASSA_AUTOPAY_SUBSCRIPTION_NOT_FOUND)
    return {"ok": True}


@router.get("/admin/attempts")
async def admin_attempts(session: AsyncSession = Depends(get_session), identity=Depends(verify_identity_admin)):
    attempts = await dal.list_unfinished_attempts(session)
    return {
        "items": [
            {
                "id": item.id,
                "user_id": item.user_id,
                "subscription_id": item.subscription_id,
                "payment_id": item.payment_id,
                "status": item.status,
                "reserved_balance": (item.intent or {}).get("reserved_balance", 0),
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in attempts
        ]
    }


@router.post("/admin/attempts/{attempt_id}/resolve")
async def admin_resolve(attempt_id: str, body: ResolveRequest, identity=Depends(verify_identity_admin)):
    result = await resolve_unknown_attempt(attempt_id, body.payment_id)
    if not result.ok:
        raise HTTPException(status_code=400, detail=result.error or texts.YOOKASSA_AUTOPAY_API_PAYMENT_UNCONFIRMED)
    return {"ok": True, "already_processed": result.already_processed, "message": result.error}


@router.delete("/cards/{card_id}")
async def remove_card(
    card_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    uid = await resolve_billing_user_id(request, identity, session)
    if not await dal.delete_card(session, uid, card_id):
        raise HTTPException(status_code=404, detail=texts.YOOKASSA_AUTOPAY_CARD_NOT_FOUND)
    return {"ok": True}
