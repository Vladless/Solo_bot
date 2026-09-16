import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import (
    BaseModel,
    Field as PydanticField,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import get_session, verify_identity_token
from api.v2.routes.auth._common import _client_ip
from mail import send_email_verify_code_email, smtp_configured
from utils.web_email_codes import email_verify_codes as verify_util


router = APIRouter()


class VerifyEmailRequest(BaseModel):
    code: str = PydanticField(..., min_length=1, max_length=10)


@router.post("/send-verify-code")
async def send_email_verify_code(
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Отправить код подтверждения email. Требует авторизации."""
    if not smtp_configured():
        raise HTTPException(status_code=503, detail="Почтовый сервер не настроен")
    email = (identity.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email не привязан к аккаунту")
    if getattr(identity, "email_verified", False):
        return {"ok": True, "detail": "Email уже подтверждён"}
    if not await verify_util.redis_ready():
        raise HTTPException(status_code=503, detail="Сервис временно недоступен")
    ip = _client_ip(request)
    if not await verify_util.try_consume_ip_budget(ip):
        raise HTTPException(status_code=429, detail="Слишком много запросов, попробуйте позже")
    if not await verify_util.try_consume_email_send_budget(email):
        raise HTTPException(status_code=429, detail="Слишком много запросов на этот email")
    if not await verify_util.try_acquire_cooldown(email):
        raise HTTPException(status_code=429, detail="Подождите минуту перед повторной отправкой")
    code = f"{secrets.randbelow(900000) + 100000}"
    await verify_util.store_code(email, code)
    try:
        await send_email_verify_code_email(email, code)
    except Exception:
        await verify_util.delete_code(email)
        raise HTTPException(status_code=503, detail="Не удалось отправить письмо")
    return {"ok": True}


@router.post("/verify-email")
async def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Подтвердить email по коду."""
    email = (identity.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email не привязан к аккаунту")
    if getattr(identity, "email_verified", False):
        return {"ok": True, "detail": "Email уже подтверждён"}
    if not await verify_util.try_consume_verify_budget(email):
        raise HTTPException(status_code=429, detail="Слишком много попыток, попробуйте позже")
    if not await verify_util.verify_and_consume_code(email, body.code.strip()):
        raise HTTPException(status_code=400, detail="Неверный или просроченный код")
    from sqlalchemy import update

    from database.models import Identity as IdentityModel

    await session.execute(update(IdentityModel).where(IdentityModel.id == identity.id).values(email_verified=True))
    return {"ok": True}
