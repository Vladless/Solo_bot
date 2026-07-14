import hashlib

from collections.abc import AsyncGenerator
from datetime import datetime
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit import set_api_actor
from core.settings.runtime_sync import maybe_sync_runtime_configs
from database import (
    async_session_maker,
    identities as idb,
    identity_sessions as idsess,
)
from database.access.resolution import ResolvedActor, resolve_actor_from_identity
from database.models import Admin, Identity
from logger import logger


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    try:
        await maybe_sync_runtime_configs()
    except Exception as exc:
        logger.debug("[Depends] runtime config sync failed: {}", exc)
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def bind_identity_actor(
    request: Request | None,
    session: AsyncSession,
    identity: Identity,
) -> ResolvedActor:
    actor = await resolve_actor_from_identity(session, identity)
    set_api_actor(request, identity_id=actor.identity_id, tg_id=actor.telegram_chat_id)
    if request is not None:
        request.state.actor = actor
    return actor


def get_request_actor(request: Request | None) -> ResolvedActor | None:
    if request is None:
        return None
    return getattr(request.state, "actor", None)


async def verify_admin_token(
    admin_id: int = Query(..., alias="tg_id"),
    token: str = Header(..., alias="X-Token"),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
) -> Admin:
    hashed = hash_token(token)
    result = await session.execute(select(Admin).where(Admin.tg_id == admin_id, Admin.token == hashed))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=401, detail="Unauthorized")
    set_api_actor(request, tg_id=admin.tg_id)
    return admin


AUTH_COOKIE_NAME = "auth_token"


IS_ADMIN_COOKIE_NAME = "is_admin"


AUTH_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


def _is_secure_request(request: Request | None) -> bool:
    if request is None:
        return False
    if request.url.scheme == "https":
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    return forwarded_proto == "https"


def set_auth_cookie(response: Response, token: str, request: Request | None = None) -> None:
    """Устанавливает HttpOnly cookie с auth-токеном на ответ. Используется во всех login-ручках."""
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
        path="/",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )


def clear_auth_cookie(response: Response, request: Request | None = None) -> None:
    """Удаляет auth cookie на стороне браузера."""
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )

    clear_is_admin_cookie(response, request)


def set_is_admin_cookie(response: Response, identity: Identity, request: Request | None = None) -> None:
    """Ставит/гасит `is_admin` cookie в зависимости от текущей identity."""
    if getattr(identity, "is_admin", False):
        response.set_cookie(
            key=IS_ADMIN_COOKIE_NAME,
            value="1",
            max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
            path="/",
            httponly=True,
            secure=_is_secure_request(request),
            samesite="lax",
        )
    else:
        clear_is_admin_cookie(response, request)


def clear_is_admin_cookie(response: Response, request: Request | None = None) -> None:
    response.delete_cookie(
        key=IS_ADMIN_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )


def _read_auth_cookie(request: Request | None) -> str | None:
    if request is None:
        return None
    raw = request.cookies.get(AUTH_COOKIE_NAME)
    if not raw:
        return None
    raw = raw.strip()
    return raw or None


async def _identity_from_cookie(session: AsyncSession, request: Request | None) -> Identity | None:
    token = _read_auth_cookie(request)
    if not token:
        return None
    token_hash = hash_token(token)
    sess = await idsess.get_session_by_token_hash(session, token_hash)
    if sess is None:
        return None
    if sess.expires_at is not None and sess.expires_at <= datetime.utcnow():
        return None
    identity = await idb.get_identity_by_id(session, sess.identity_id)
    if identity is None:
        return None
    await idsess.touch_session_last_seen(session, sess)
    if request is not None:
        try:
            request.state.auth_session = sess
        except Exception:
            pass
    return identity


async def verify_identity_token(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Проверяет токен из HttpOnly cookie `auth_token`; возвращает Identity."""
    from database.site_state import mark_site_initialized

    identity = await _identity_from_cookie(session, request)
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    await bind_identity_actor(request, session, identity)
    if getattr(identity, "is_admin", False):
        await mark_site_initialized(session)
    return identity


async def resolve_identity_role(session: AsyncSession, identity: Identity) -> str:
    tg_id = getattr(identity, "tg_id", None)
    if tg_id:
        try:
            row = (await session.execute(select(Admin).where(Admin.tg_id == int(tg_id)))).scalar_one_or_none()
        except Exception:
            row = None
        if row is not None:
            role = (getattr(row, "role", None) or "admin").strip().lower()
            return "moderator" if role == "moderator" else "admin"
    if getattr(identity, "is_admin", False):
        return "admin"
    return "user"


async def verify_identity_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Проверяет токен из cookie и что identity.is_admin; для админских ручек v2."""
    from database.site_state import mark_site_initialized

    identity = await _identity_from_cookie(session, request)
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    if await resolve_identity_role(session, identity) == "moderator":
        raise HTTPException(status_code=403, detail="Forbidden")
    await bind_identity_actor(request, session, identity)
    await mark_site_initialized(session)
    return identity


async def verify_identity_agent(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Пускает суперадмина и модератора (для раздела тикетов /support)."""
    from database.site_state import mark_site_initialized

    identity = await _identity_from_cookie(session, request)
    if identity is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if await resolve_identity_role(session, identity) not in ("admin", "moderator"):
        raise HTTPException(status_code=403, detail="Forbidden")
    await bind_identity_actor(request, session, identity)
    await mark_site_initialized(session)
    return identity


async def verify_identity_admin_short(
    request: Request,
):
    """Проверка админа с короткой сессией (для broadcast и др.), чтобы не держать соединение с БД."""
    identity = None
    actor = None
    async with async_session_maker() as session:
        identity = await _identity_from_cookie(session, request)
        if identity:
            actor = await resolve_actor_from_identity(session, identity)
        await session.commit()
    if not identity:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    if actor is not None:
        set_api_actor(request, identity_id=identity.id, tg_id=actor.telegram_chat_id)
        if request is not None:
            request.state.actor = actor
    else:
        set_api_actor(request, identity_id=identity.id, tg_id=identity.tg_id)
    return identity


async def verify_admin_token_short(
    admin_id: int = Query(..., alias="tg_id"),
    token: str = Header(..., alias="X-Token"),
    request: Request = None,
) -> Admin:
    """Проверка админа с короткой сессией (для broadcast и др.), чтобы не держать соединение с БД."""
    hashed = hash_token(token)
    async with async_session_maker() as session:
        result = await session.execute(select(Admin).where(Admin.tg_id == admin_id, Admin.token == hashed))
        admin = result.scalar_one_or_none()
        await session.commit()
    if not admin:
        raise HTTPException(status_code=401, detail="Unauthorized")
    set_api_actor(request, tg_id=admin.tg_id)
    return admin


def validate_redirect_url(url: str, base_url: str) -> str:
    """Validate redirect URL is same-origin or relative. Returns safe URL or base_url fallback."""
    url = url.strip()
    if not url:
        return base_url
    if url.startswith("/"):
        return url
    try:
        parsed = urlparse(url)
        base_parsed = urlparse(base_url)
        if parsed.scheme in ("http", "https") and parsed.netloc == base_parsed.netloc:
            return url
    except Exception:
        pass
    return base_url
