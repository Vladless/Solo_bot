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
from api.v2.routes.auth._common import _client_ip, safe_return_path


_YANDEX_NONCE_COOKIE = "y_oauth_nonce"
from database import identities as idb
from logger import logger


try:
    from settings.config import YANDEX_CLIENT_ID as _YANDEX_CLIENT_ID
except ImportError:
    _YANDEX_CLIENT_ID = ""
try:
    from settings.config import YANDEX_CLIENT_SECRET as _YANDEX_CLIENT_SECRET
except ImportError:
    _YANDEX_CLIENT_SECRET = ""
try:
    from settings.config import YANDEX_REDIRECT_URI as _YANDEX_REDIRECT_URI
except ImportError:
    _YANDEX_REDIRECT_URI = ""
try:
    from settings.config import OAUTH_SUCCESS_URI as _OAUTH_SUCCESS_URI
except ImportError:
    _OAUTH_SUCCESS_URI = "/dashboard"
try:
    from settings.config import API_TOKEN as _API_TOKEN_RAW
except ImportError:
    _API_TOKEN_RAW = ""
_YANDEX_STATE_KEY = str(_API_TOKEN_RAW or "").strip() or secrets.token_hex(32)
_YANDEX_STATE_SECRET = hashlib.sha256(b"oauth-state:yandex:v1:" + _YANDEX_STATE_KEY.encode()).hexdigest()


YANDEX_AUTH_ENDPOINT = "https://oauth.yandex.ru/authorize"
YANDEX_TOKEN_ENDPOINT = "https://oauth.yandex.ru/token"
YANDEX_USERINFO_ENDPOINT = "https://login.yandex.ru/info"
STATE_TTL_SECONDS = 600


router = APIRouter()


def yandex_configured() -> bool:
    return bool(_YANDEX_CLIENT_ID and _YANDEX_CLIENT_SECRET and _YANDEX_REDIRECT_URI)


def _sign_state(payload: str) -> str:
    mac = hmac.new(str(_YANDEX_STATE_SECRET).encode(), payload.encode(), hashlib.sha256).digest()
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


@router.get("/yandex/authorize")
async def yandex_authorize(
    request: Request,
    return_to: str = Query(default=""),
):
    """Начинает OAuth-флоу Яндекс ID: редиректит юзера на consent screen."""
    if not yandex_configured():
        raise HTTPException(status_code=503, detail="Вход через Яндекс не настроен на этом сервере")
    safe_return = safe_return_path(return_to, _OAUTH_SUCCESS_URI)
    state, nonce = _make_state(safe_return)
    params = {
        "client_id": _YANDEX_CLIENT_ID,
        "redirect_uri": _YANDEX_REDIRECT_URI,
        "response_type": "code",
        "state": state,
        "force_confirm": "yes",
    }
    url = f"{YANDEX_AUTH_ENDPOINT}?{urlencode(params)}"
    logger.info("[Auth] Yandex authorize: ip={}", _client_ip(request))
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(
        key=_YANDEX_NONCE_COOKIE,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        path="/",
        httponly=True,
        secure=_is_secure_request(request),
        samesite="lax",
    )
    return resp


@router.get("/yandex/callback")
async def yandex_callback(
    request: Request,
    response: Response,
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    session: AsyncSession = Depends(get_session),
):
    """Коллбек Яндекс ID: обмен code → token → userinfo → identity."""
    if not yandex_configured():
        raise HTTPException(status_code=503, detail="Вход через Яндекс не настроен на этом сервере")
    if error:
        logger.warning("[Auth] Yandex callback error: {} ip={}", error, _client_ip(request))
        return RedirectResponse(f"/login?error=yandex_{error}", status_code=302)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Отсутствует code или state")
    verified = _verify_state(state)
    if verified is None:
        logger.warning("[Auth] Yandex callback: invalid/expired state ip={}", _client_ip(request))
        raise HTTPException(status_code=400, detail="Неверный или просроченный state")
    return_to, state_nonce = verified
    cookie_nonce = (request.cookies.get(_YANDEX_NONCE_COOKIE) or "").strip()
    if not cookie_nonce or not hmac.compare_digest(cookie_nonce, state_nonce):
        logger.warning("[Auth] Yandex callback: state/cookie nonce mismatch ip={}", _client_ip(request))
        raise HTTPException(status_code=400, detail="Неверный или просроченный state")

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            token_res = await client.post(
                YANDEX_TOKEN_ENDPOINT,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": _YANDEX_CLIENT_ID,
                    "client_secret": _YANDEX_CLIENT_SECRET,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as e:
            logger.warning("[Auth] Yandex token exchange network error: {}", e)
            raise HTTPException(status_code=502, detail="Не удалось связаться с Яндекс") from e
        if token_res.status_code != 200:
            logger.warning("[Auth] Yandex token exchange failed: {} {}", token_res.status_code, token_res.text[:200])
            raise HTTPException(status_code=401, detail="Яндекс отклонил токен")
        token_payload = token_res.json()
        access_token = token_payload.get("access_token")
        if not access_token:
            raise HTTPException(status_code=401, detail="Яндекс не вернул access_token")

        try:
            info_res = await client.get(
                YANDEX_USERINFO_ENDPOINT,
                headers={"Authorization": f"OAuth {access_token}"},
                params={"format": "json"},
            )
        except httpx.HTTPError as e:
            logger.warning("[Auth] Yandex userinfo network error: {}", e)
            raise HTTPException(status_code=502, detail="Не удалось получить профиль Яндекс") from e

    if info_res.status_code != 200:
        raise HTTPException(status_code=401, detail="Яндекс не вернул профиль")
    info = info_res.json()
    yandex_sub = str(info.get("id") or "").strip()
    email = (info.get("default_email") or "").strip().lower() or None
    if not yandex_sub:
        raise HTTPException(status_code=401, detail="Яндекс не вернул идентификатор пользователя")

    identity = await idb.get_or_create_identity_for_yandex(
        session,
        yandex_sub=yandex_sub,
        email=email,
    )
    await bind_identity_actor(request, session, identity)
    token = await idb.issue_token_for_identity(session, identity, request=request)
    logger.info(
        "[Auth] Login success: identity={}, yandex_sub={}, ip={}, method=yandex",
        identity.id,
        yandex_sub,
        _client_ip(request),
    )
    redirect = RedirectResponse(safe_return_path(return_to, _OAUTH_SUCCESS_URI), status_code=302)
    redirect.delete_cookie(_YANDEX_NONCE_COOKIE, path="/")
    set_auth_cookie(redirect, token, request)
    set_is_admin_cookie(redirect, identity, request)
    return redirect


@router.get("/yandex/status")
async def yandex_status():
    """Позволяет фронтенду узнать, настроен ли вход через Яндекс на этом сервере."""
    return {"enabled": yandex_configured()}
