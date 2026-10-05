from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_admin
from api.v2.schemas.identities import (
    IdentityAttachEmail,
    IdentityAttachTelegram,
    IdentityCreate,
    IdentityResponse,
)
from database import identities as idb


router = APIRouter(tags=["Identities"])


@router.post("/", response_model=IdentityResponse)
async def create_identity(
    body: IdentityCreate,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Создаёт идентичность; можно указать email и/или tg_id."""
    email = body.email.strip().lower() if body.email and body.email.strip() else None
    if not email and not body.tg_id:
        raise HTTPException(status_code=400, detail="Укажите email и/или tg_id")
    identity = await idb.create_identity(session, email=email, tg_id=body.tg_id)
    return IdentityResponse.model_validate(identity)


@router.get("/{identity_id}", response_model=IdentityResponse)
async def get_identity(
    identity_id: str = Path(...),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Возвращает идентичность по id."""
    identity = await idb.get_identity_by_id(session, identity_id)
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")
    return IdentityResponse.model_validate(identity)


@router.get("/by/email", response_model=IdentityResponse)
async def get_identity_by_email(
    email: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Возвращает идентичность по email."""
    identity = await idb.get_identity_by_email(session, email)
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")
    return IdentityResponse.model_validate(identity)


@router.get("/by/tg_id/{tg_id}", response_model=IdentityResponse)
async def get_identity_by_tg_id(
    tg_id: int = Path(...),
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Возвращает идентичность по Telegram ID."""
    identity = await idb.get_identity_by_tg_id(session, tg_id)
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")
    return IdentityResponse.model_validate(identity)


@router.patch("/{identity_id}/attach-email", response_model=IdentityResponse)
async def attach_email(
    identity_id: str = Path(...),
    body: IdentityAttachEmail = ...,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Привязывает email к идентичности."""
    identity = await idb.attach_email(session, identity_id, body.email)
    if not identity:
        raise HTTPException(
            status_code=404,
            detail="Identity not found или email уже привязан к другой идентичности",
        )
    return IdentityResponse.model_validate(identity)


@router.patch("/{identity_id}/attach-telegram", response_model=IdentityResponse)
async def attach_telegram(
    identity_id: str = Path(...),
    body: IdentityAttachTelegram = ...,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_admin),
):
    """Привязывает Telegram (tg_id) к идентичности."""
    identity = await idb.attach_telegram(session, identity_id, body.tg_id)
    if not identity:
        raise HTTPException(
            status_code=404,
            detail="Identity not found или tg_id уже привязан к другой идентичности",
        )
    return IdentityResponse.model_validate(identity)
