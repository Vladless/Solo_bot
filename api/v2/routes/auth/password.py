import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    bind_identity_actor,
    get_session,
    set_auth_cookie,
    set_is_admin_cookie,
)
from api.v2.routes.auth._common import TOKEN_TTL_HINT, _client_ip, build_login_response
from api.v2.schemas.identities import (
    ConfirmPasswordResetRequest,
    LoginByCodeRequest,
    LoginRequest,
    LoginResponse,
    RegisterByEmailRequest,
    RegisterResponse,
    SendLoginCodeRequest,
)
from core.client_origin import INVITE_REFERRAL, set_client_invite
from database import (
    add_referral,
    get_referral_by_referred_id,
    identities as idb,
    identity_sessions as idsess,
)
from database.access.resolution import resolve_user_optional
from logger import logger
from mail import (
    send_email_verify_code_email,
    send_login_code_email,
    send_password_reset_code_email,
    smtp_configured,
)
from utils import (
    web_email_verify_code as email_verify,
    web_password_reset_code as pwd_reset,
)
from utils.disposable_emails import is_disposable_email
from utils.referral_codes import decode_referral_code
from utils.turnstile import turnstile_enabled, verify_turnstile_token
from utils.web_login_code import (
    delete_code,
    normalize_login_email,
    redis_ready_for_login_codes,
    release_resend_cooldown,
    store_code,
    try_acquire_resend_cooldown,
    try_consume_email_send_budget,
    try_consume_email_verify_budget,
    try_consume_ip_send_budget,
    verify_and_consume_code,
)


router = APIRouter()

_RESET_OK_MESSAGE = {
    "ok": True,
    "message": "Если для этого адреса есть аккаунт с паролем, мы отправили код. Проверьте почту.",
}


@router.post("/register", response_model=RegisterResponse)
async def register_by_email(
    body: RegisterByEmailRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    (
        """Регистрация по почте и паролю: создаётся идентичность, выдаётся токен. Срок действия токена: """
        + TOKEN_TTL_HINT
        + "."
    )
    ip = _client_ip(request)
    try:
        from core.rate_limit import check_and_increment
        from core.redis_cache import cache_incr_checked

        count, redis_ok = await cache_incr_checked(f"register_rate:{ip}", 3600)
        if not redis_ok:
            count = check_and_increment(f"register_rate:{ip}", 5, 3600)
        if count > 5:
            raise HTTPException(status_code=429, detail="Слишком много регистраций с этого IP. Попробуйте позже.")
    except HTTPException:
        raise
    except Exception:
        pass
    if turnstile_enabled():
        if not await verify_turnstile_token(body.turnstile_token, ip):
            raise HTTPException(status_code=400, detail="Проверка CAPTCHA не пройдена")
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email обязателен")
    if is_disposable_email(email):
        raise HTTPException(status_code=400, detail="Одноразовые email-адреса не поддерживаются")
    if not body.password or len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Пароль минимум 8 символов")
    existing = await idb.get_identity_by_email(session, email)
    if existing:
        raise HTTPException(status_code=409, detail="Идентичность с таким email уже существует")
    raw_referral = str(body.referral_code or "").strip()
    if "/referral/" in raw_referral:
        raw_referral = raw_referral.split("/referral/", 1)[-1]
    if "start=referral_" in raw_referral:
        raw_referral = raw_referral.split("start=referral_", 1)[-1]
    raw_referral = raw_referral.split("?", 1)[0].split("#", 1)[0].strip()
    referrer_legacy = decode_referral_code(raw_referral)
    referrer_user = None
    if body.referral_code and referrer_legacy is None:
        raise HTTPException(status_code=400, detail="Код приглашения недействителен")
    if referrer_legacy is not None:
        referrer_user = await resolve_user_optional(session, referrer_legacy)
        if referrer_user is None:
            raise HTTPException(status_code=400, detail="Код приглашения недействителен")
    if referrer_user is not None:
        set_client_invite(INVITE_REFERRAL, referrer_user.tg_id or referrer_user.id)
    identity, token = await idb.create_identity_with_token(
        session, email=email, password=body.password, request=request
    )
    await bind_identity_actor(request, session, identity)
    billing_user_id = await idb.ensure_billing_user_for_identity(session, identity)
    if referrer_user is not None and not await get_referral_by_referred_id(session, billing_user_id):
        await add_referral(session, billing_user_id, referrer_user.id)
        if referrer_user.tg_id is not None:
            try:
                from database.web_notifications import notify_web

                await notify_web(
                    session,
                    tg_id=int(referrer_user.tg_id),
                    type="referral_joined",
                    title="Ваш реферал присоединился",
                    message="Новый пользователь зарегистрировался по вашей реферальной ссылке.",
                    data={"referred_user_id": int(billing_user_id)},
                )
            except Exception:
                pass
    if smtp_configured():
        try:
            code = f"{secrets.randbelow(900000) + 100000}"
            await email_verify.store_code(email, code)
            await send_email_verify_code_email(email, code)
        except Exception as e:
            logger.warning("[Auth] Не удалось отправить код подтверждения email при регистрации: {}", e)
    logger.info("[Auth] Register success: identity={}, email={}, ip={}", identity.id, email, _client_ip(request))
    set_auth_cookie(response, token, request)
    set_is_admin_cookie(response, identity, request)
    return RegisterResponse(identity_id=identity.id)


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Вход по email и паролю. Возвращает identity_id и новый токен. Срок действия токена: """ + TOKEN_TTL_HINT + "."
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email обязателен")
    ip = _client_ip(request)
    try:
        from core.rate_limit import check_and_increment
        from core.redis_cache import cache_get, cache_incr_checked

        lockout_key = f"login_lockout:{email}"
        locked = await cache_get(lockout_key)
        if locked:
            raise HTTPException(status_code=429, detail="Аккаунт временно заблокирован. Попробуйте через 15 минут.")
        rkey = f"login_pwd_rate:{ip}"
        count, redis_ok = await cache_incr_checked(rkey, 900)
        if not redis_ok:
            count = check_and_increment(rkey, 10, 900)
        if count > 10:
            raise HTTPException(status_code=429, detail="Слишком много попыток. Попробуйте позже.")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[Auth] Ошибка rate-limit проверки для email-логина: {}", e)
    result = await idb.login_by_email(session, email, body.password, request=request)
    if not result:
        from database.setup.web_admin_bootstrap import ensure_web_admin

        try:
            await ensure_web_admin(session)
            await session.flush()
            result = await idb.login_by_email(session, email, body.password, request=request)
        except Exception as exc:
            logger.warning("[Auth] lazy web-admin bootstrap failed: {}", exc)
    if not result:
        try:
            from core.redis_cache import cache_incr, cache_set

            fail_key = f"login_fail:{email}"
            fails = await cache_incr(fail_key, 900)
            if fails >= 10:
                await cache_set(f"login_lockout:{email}", "1", 900)
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    try:
        from core.redis_cache import cache_delete

        await cache_delete(f"login_fail:{email}")
    except Exception:
        pass
    identity, token = result
    await bind_identity_actor(request, session, identity)
    if getattr(identity, "is_admin", False):
        from database.site_state import mark_site_initialized

        await mark_site_initialized(session)
    logger.info("[Auth] Login success: identity={}, email={}, ip={}, method=password", identity.id, email, ip)
    set_auth_cookie(response, token, request)
    set_is_admin_cookie(response, identity, request)
    return build_login_response(identity)


@router.post("/send-login-code")
async def send_login_code(
    body: SendLoginCodeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Отправить код входа на email (SMTP + Redis)."""
    ip = _client_ip(request)
    try:
        from core.rate_limit import check_and_increment
        from core.redis_cache import cache_incr_checked

        count, redis_ok = await cache_incr_checked(f"send_code_rate:{ip}", 3600)
        if not redis_ok:
            count = check_and_increment(f"send_code_rate:{ip}", 10, 3600)
        if count > 10:
            raise HTTPException(status_code=429, detail="Слишком много запросов кодов. Попробуйте позже.")
    except HTTPException:
        raise
    except Exception:
        pass
    if turnstile_enabled():
        if not await verify_turnstile_token(body.turnstile_token, ip):
            raise HTTPException(status_code=400, detail="Проверка CAPTCHA не пройдена")
    email_norm = normalize_login_email(body.email)
    if not email_norm:
        raise HTTPException(status_code=400, detail="Email обязателен")
    if is_disposable_email(email_norm):
        raise HTTPException(status_code=400, detail="Одноразовые email-адреса не поддерживаются")
    if not smtp_configured():
        raise HTTPException(
            status_code=503,
            detail="Отправка кода недоступна: почта не настроена на сервере",
        )
    if not await redis_ready_for_login_codes():
        raise HTTPException(
            status_code=503,
            detail="Сервис временно недоступен. Попробуйте позже.",
        )
    identity = await idb.get_identity_by_email(session, email_norm)
    if not identity:
        if not body.allow_register:
            return {"ok": True, "message": "Код отправлен на почту"}
        identity = await idb.create_identity(session, email=email_norm)
    ip = _client_ip(request)
    if not await try_consume_ip_send_budget(ip):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов с вашего адреса. Попробуйте позже.",
        )
    if not await try_consume_email_send_budget(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов для этого адреса. Попробуйте позже.",
        )
    if not await try_acquire_resend_cooldown(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Код уже отправлен. Подождите перед повторной отправкой.",
        )
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    if not await store_code(email_norm, code):
        await release_resend_cooldown(email_norm)
        raise HTTPException(
            status_code=503,
            detail="Не удалось сохранить код. Попробуйте позже.",
        )
    try:
        await send_login_code_email(email_norm, code)
    except Exception:
        await release_resend_cooldown(email_norm)
        await delete_code(email_norm)
        raise HTTPException(
            status_code=503,
            detail="Не удалось отправить письмо. Попробуйте позже.",
        ) from None
    return {"ok": True, "message": "Код отправлен на почту"}


@router.post("/login-by-code", response_model=LoginResponse)
async def login_by_code(
    body: LoginByCodeRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Вход по email и коду из письма."""
    email_norm = normalize_login_email(body.email)
    if not email_norm or not body.code or not body.code.strip():
        raise HTTPException(status_code=400, detail="Email и код обязательны")
    if not await redis_ready_for_login_codes():
        raise HTTPException(
            status_code=503,
            detail="Сервис временно недоступен. Попробуйте позже.",
        )
    if not await try_consume_email_verify_budget(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток. Запросите новый код.",
        )
    if not await verify_and_consume_code(email_norm, body.code.strip()):
        raise HTTPException(status_code=401, detail="Неверный код или срок действия истёк")
    identity = await idb.get_identity_by_email(session, email_norm)
    if not identity:
        raise HTTPException(status_code=401, detail="Аккаунт не найден")
    if not getattr(identity, "email_verified", False):
        from sqlalchemy import update as sa_update

        from database.models import Identity as IdentityModel

        await session.execute(
            sa_update(IdentityModel).where(IdentityModel.id == identity.id).values(email_verified=True)
        )
    await bind_identity_actor(request, session, identity)
    token = await idb.issue_token_for_identity(session, identity, request=request)
    if getattr(identity, "is_admin", False):
        from database.site_state import mark_site_initialized

        await mark_site_initialized(session)
    logger.info("[Auth] Login success: identity={}, email={}, method=code", identity.id, email_norm)
    set_auth_cookie(response, token, request)
    set_is_admin_cookie(response, identity, request)
    return build_login_response(identity)


@router.post("/request-password-reset")
async def request_password_reset(
    body: SendLoginCodeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    email_norm = normalize_login_email(body.email)
    if not email_norm:
        raise HTTPException(status_code=400, detail="Email обязателен")
    if not smtp_configured() or not await pwd_reset.redis_ready():
        return _RESET_OK_MESSAGE
    identity = await idb.get_identity_by_email(session, email_norm)
    if not identity:
        return _RESET_OK_MESSAGE
    ip = _client_ip(request)
    if not await pwd_reset.try_consume_ip_budget(ip):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов с вашего адреса. Попробуйте позже.",
        )
    if not await pwd_reset.try_consume_email_send_budget(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Слишком много запросов для этого адреса. Попробуйте позже.",
        )
    if not await pwd_reset.try_acquire_cooldown(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Код уже отправлен. Подождите перед повторной отправкой.",
        )
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    if not await pwd_reset.store_code(email_norm, code):
        await pwd_reset.release_cooldown(email_norm)
        raise HTTPException(
            status_code=503,
            detail="Не удалось сохранить код. Попробуйте позже.",
        )
    try:
        await send_password_reset_code_email(email_norm, code)
    except Exception:
        await pwd_reset.release_cooldown(email_norm)
        await pwd_reset.delete_code(email_norm)
        raise HTTPException(
            status_code=503,
            detail="Не удалось отправить письмо. Попробуйте позже.",
        ) from None
    return _RESET_OK_MESSAGE


@router.post("/confirm-password-reset", response_model=LoginResponse)
async def confirm_password_reset(
    body: ConfirmPasswordResetRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    email_norm = normalize_login_email(body.email)
    if not email_norm or not body.code or not body.code.strip():
        raise HTTPException(status_code=400, detail="Email и код обязательны")
    if body.password != body.password_confirm:
        raise HTTPException(status_code=400, detail="Пароли не совпадают")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Пароль минимум 8 символов")
    if not await pwd_reset.redis_ready():
        raise HTTPException(
            status_code=503,
            detail="Сервис временно недоступен. Попробуйте позже.",
        )
    if not await pwd_reset.try_consume_email_verify_budget(email_norm):
        raise HTTPException(
            status_code=429,
            detail="Слишком много попыток. Запросите новый код.",
        )
    if not await pwd_reset.verify_and_consume_code(email_norm, body.code.strip()):
        raise HTTPException(status_code=401, detail="Неверный код или срок действия истёк")
    identity = await idb.get_identity_by_email(session, email_norm)
    if not identity:
        raise HTTPException(status_code=400, detail="Аккаунт не найден")
    updated = await idb.set_password_for_identity(session, identity.id, body.password)
    if not updated:
        raise HTTPException(status_code=400, detail="Не удалось обновить пароль")
    await bind_identity_actor(request, session, updated)
    token = await idb.issue_token_for_identity(session, updated, request=request)
    await idsess.delete_other_sessions(session, identity_id=updated.id, keep_token_hash=idb.hash_token(token))
    set_auth_cookie(response, token, request)
    set_is_admin_cookie(response, updated, request)
    return build_login_response(updated)
