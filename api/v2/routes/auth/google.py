import base64
import hashlib
import hmac
import secrets
import time

from urllib.parse import urlencode

import httpx

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    _is_secure_request,
    bind_identity_actor,
    get_session,
    set_auth_cookie,
    set_is_admin_cookie,
)


_GOOGLE_NONCE_COOKIE = "g_oauth_nonce"
from api.v2.routes.auth._common import _client_ip, safe_return_path
from database import identities as idb
from logger import logger


try:
    from settings.config import GOOGLE_CLIENT_ID as _GOOGLE_CLIENT_ID
except ImportError:
    _GOOGLE_CLIENT_ID = ""
try:
    from settings.config import GOOGLE_CLIENT_SECRET as _GOOGLE_CLIENT_SECRET
except ImportError:
    _GOOGLE_CLIENT_SECRET = ""
try:
    from settings.config import GOOGLE_REDIRECT_URI as _GOOGLE_REDIRECT_URI
except ImportError:
    _GOOGLE_REDIRECT_URI = ""
try:
    from settings.config import OAUTH_SUCCESS_URI as _OAUTH_SUCCESS_URI
except ImportError:
    _OAUTH_SUCCESS_URI = "/dashboard"
try:
    from settings.config import API_TOKEN as _API_TOKEN_RAW
except ImportError:
    _API_TOKEN_RAW = ""
_GOOGLE_STATE_KEY = str(_API_TOKEN_RAW or "").strip() or secrets.token_hex(32)
_GOOGLE_STATE_SECRET = hashlib.sha256(b"oauth-state:google:v1:" + _GOOGLE_STATE_KEY.encode()).hexdigest()


GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
STATE_TTL_SECONDS = 600


router = APIRouter()


def google_configured() -> bool:
    return bool(_GOOGLE_CLIENT_ID and _GOOGLE_CLIENT_SECRET and _GOOGLE_REDIRECT_URI)


def _sign_state(payload: str) -> str:
    mac = hmac.new(str(_GOOGLE_STATE_SECRET).encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def _make_state(return_to: str) -> tuple[str, str]:
    nonce = secrets.token_urlsafe(16)
    ts = str(int(time.time()))
    payload = f"{nonce}.{ts}.{return_to}"
    sig = _sign_state(payload)
    raw = f"{payload}.{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("="), nonce


def _verify_state(state: str) -> tuple[str, str] | None:
    try:
        padded = state + "=" * (-len(state) % 4)
        raw = base64.urlsafe_b64decode(padded).decode()
    except Exception:
        return None
    parts = raw.rsplit(".", 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    expected = _sign_state(payload)
    if not hmac.compare_digest(sig, expected):
        return None
    chunks = payload.split(".", 2)
    if len(chunks) != 3:
        return None
    _nonce, ts, return_to = chunks
    try:
        if int(time.time()) - int(ts) > STATE_TTL_SECONDS:
            return None
    except Exception:
        return None
    return (return_to or _OAUTH_SUCCESS_URI, _nonce)


@router.get("/google/authorize")
async def google_authorize(
    request: Request,
    return_to: str = Query(default=""),
):
    """Начинает OAuth-флоу Google: редиректит юзера на Google consent screen."""
    if not google_configured():
        raise HTTPException(status_code=503, detail="Google Sign-In не настроен на этом сервере")
    safe_return = safe_return_path(return_to, _OAUTH_SUCCESS_URI)
    state, nonce = _make_state(safe_return)
    params = {
        "client_id": _GOOGLE_CLIENT_ID,
        "redirect_uri": _GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"
    logger.info("[Auth] Google authorize: ip={}", _client_ip(request))
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        key=_GOOGLE_NONCE_COOKIE,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        path="/",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )
    return resp


@router.get("/google/callback")
async def google_callback(
    request: Request,
    response: Response,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Коллбек Google: обмен code → token → userinfo → identity."""
    if not google_configured():
        raise HTTPException(status_code=503, detail="Google Sign-In не настроен на этом сервере")
    if error:
        logger.warning("[Auth] Google callback error: {} ip={}", error, _client_ip(request))
        return RedirectResponse(f"/login?error=google_{error}", status_code=302)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Отсутствует code или state")
    verified = _verify_state(state)
    if verified is None:
        logger.warning("[Auth] Google callback: invalid/expired state ip={}", _client_ip(request))
        raise HTTPException(status_code=400, detail="Неверный или просроченный state")
    return_to, state_nonce = verified
    cookie_nonce = (request.cookies.get(_GOOGLE_NONCE_COOKIE) or "").strip()
    if not cookie_nonce or not hmac.compare_digest(cookie_nonce, state_nonce):
        logger.warning("[Auth] Google callback: state/cookie nonce mismatch ip={}", _client_ip(request))
        raise HTTPException(status_code=400, detail="Неверный или просроченный state")

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            token_res = await client.post(
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": _GOOGLE_CLIENT_ID,
                    "client_secret": _GOOGLE_CLIENT_SECRET,
                    "redirect_uri": _GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            logger.warning("[Auth] Google token exchange network error: {}", e)
            raise HTTPException(status_code=502, detail="Не удалось связаться с Google") from e
        if token_res.status_code != 200:
            logger.warning("[Auth] Google token exchange failed: {} {}", token_res.status_code, token_res.text[:200])
            raise HTTPException(status_code=401, detail="Google отклонил токен")
        token_payload = token_res.json()
        access_token = token_payload.get("access_token")
        if not access_token:
            raise HTTPException(status_code=401, detail="Google не вернул access_token")

        try:
            info_res = await client.get(
                GOOGLE_USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as e:
            logger.warning("[Auth] Google userinfo network error: {}", e)
            raise HTTPException(status_code=502, detail="Не удалось получить профиль Google") from e

    if info_res.status_code != 200:
        raise HTTPException(status_code=401, detail="Google не вернул профиль")
    info = info_res.json()
    google_sub = str(info.get("sub") or "").strip()
    email = (info.get("email") or "").strip().lower() or None
    email_verified = bool(info.get("email_verified"))
    if not google_sub:
        raise HTTPException(status_code=401, detail="Google не вернул идентификатор пользователя")

    identity = await idb.get_or_create_identity_for_google(
        session,
        google_sub=google_sub,
        email=email if (email and email_verified) else None,
    )
    await bind_identity_actor(request, session, identity)
    token = await idb.issue_token_for_identity(session, identity, request=request)
    logger.info(
        "[Auth] Login success: identity={}, google_sub={}, ip={}, method=google",
        identity.id,
        google_sub,
        _client_ip(request),
    )
    redirect = RedirectResponse(safe_return_path(return_to, _OAUTH_SUCCESS_URI), status_code=302)
    redirect.delete_cookie(_GOOGLE_NONCE_COOKIE, path="/")
    set_auth_cookie(redirect, token, request)
    set_is_admin_cookie(redirect, identity, request)
    return redirect


@router.get("/google/status")
async def google_status():
    """Позволяет фронтенду узнать, настроен ли Google-вход на этом сервере."""
    return {"enabled": google_configured()}
