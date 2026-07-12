import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    bind_identity_actor,
    get_session,
    set_is_admin_cookie,
    verify_identity_token,
)
from api.v2.routes.auth._common import _client_ip
from api.v2.schemas.identities import (
    IdentityResponse,
    LinkEmailConfirmRequest,
    LinkEmailSendCodeRequest,
)
from database import identities as idb
from mail import send_email_link_code_email, smtp_configured
from utils import web_email_link_code as email_link_code


router = APIRouter()


@router.post("/link-email/send-code")
async def link_email_send_code(
    body: LinkEmailSendCodeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    email_norm = email_link_code.normalize_email(body.email)
    if not email_norm:
        raise HTTPException(status_code=400, detail="Укажите корректный email")
    if identity.email and str(identity.email).strip().lower() == email_norm:
        raise HTTPException(status_code=409, detail="Этот email уже привязан к аккаунту")
    if not smtp_configured():
        raise HTTPException(
            status_code=503,
            detail="Отправка кода недоступна: почта не настроена на сервере",
        )
    if not await email_link_code.redis_ready():
        raise HTTPException(
            status_code=503,
            detail="Сервис временно недоступен. Попробуйте позже.",
        )
    existing = await idb.get_identity_by_email(session, email_norm)
    if existing and existing.id != identity.id:
        our_tg = identity.tg_id
        their_tg = existing.tg_id
        can_merge = their_tg is None or (our_tg is not None and int(their_tg) == int(our_tg))
        if not can_merge:
            raise HTTPException(
                status_code=409,
                detail="Этот email уже привязан к другому аккаунту",
            )
    ip = _client_ip(request)
    if not await email_link_code.try_consume_ip_budget(ip):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов с вашего адреса. Попробуйте позже.",
        )
    if not await email_link_code.try_consume_email_send_budget(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов для этого адреса. Попробуйте позже.",
        )
    if not await email_link_code.try_acquire_cooldown(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Код уже отправлен. Подождите перед повторной отправкой.",
        )
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    if not await email_link_code.store_code(email_norm, code):
        await email_link_code.release_cooldown(email_norm)
        raise HTTPException(
            status_code=503,
            detail="Не удалось сохранить код. Попробуйте позже.",
        )
    try:
        await send_email_link_code_email(email_norm, code)
    except Exception:
        await email_link_code.release_cooldown(email_norm)
        await email_link_code.delete_code(email_norm)
        raise HTTPException(
            status_code=503,
            detail="Не удалось отправить письмо. Попробуйте позже.",
        ) from None
    return {"ok": True, "message": "Код подтверждения отправлен на почту"}


@router.post("/link-email/confirm", response_model=IdentityResponse)
async def link_email_confirm(
    body: LinkEmailConfirmRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    email_norm = email_link_code.normalize_email(body.email)
    if not email_norm or not body.code or not str(body.code).strip():
        raise HTTPException(status_code=400, detail="Email и код обязательны")
    if not await email_link_code.redis_ready():
        raise HTTPException(
            status_code=503,
            detail="Сервис временно недоступен. Попробуйте позже.",
        )
    if not await email_link_code.try_consume_email_verify_budget(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток. Запросите новый код.",
        )
    if not await email_link_code.verify_and_consume_code(email_norm, str(body.code).strip()):
        raise HTTPException(status_code=401, detail="Неверный код или срок действия истёк")
    result = await idb.attach_email(session, identity.id, email_norm)
    if not result:
        raise HTTPException(
            status_code=409,
            detail="Этот email уже привязан к другой идентичности",
        )
    await bind_identity_actor(request, session, result)
    set_is_admin_cookie(response, result, request)
    if getattr(result, "tg_id", None):
        try:
            from services.panel_identity_sync import push_identity_to_panel

            await push_identity_to_panel(session, int(result.tg_id))
        except Exception as e:
            from logger import logger

            logger.debug("[Auth] panel identity sync failed: {}", e)
    return IdentityResponse.model_validate(result)
