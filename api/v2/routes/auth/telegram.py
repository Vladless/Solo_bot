from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import (
    BaseModel,
    Field as PydanticField,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api.depends import (
    bind_identity_actor,
    get_session,
    set_auth_cookie,
    set_is_admin_cookie,
    verify_identity_token,
)
from api.v2.routes.auth._common import TELEGRAM_LOGIN_MAX_AGE, TOKEN_TTL_HINT, _client_ip, build_login_response
from api.v2.schemas.identities import (
    IdentityResponse,
    LinkTelegramRequest,
    LoginResponse,
    LoginTelegramRequest,
)
from database import identities as idb
from logger import logger
from settings.config import API_TOKEN
from utils.telegram_login import verify_telegram_login


router = APIRouter()


def _get_oidc_credentials() -> tuple[str, str]:
    try:
        from settings.config import TELEGRAM_CLIENT_ID, TELEGRAM_CLIENT_SECRET

        cid = str(TELEGRAM_CLIENT_ID).strip()
        secret = str(TELEGRAM_CLIENT_SECRET).strip()
        if cid and secret:
            return cid, secret
    except ImportError:
        pass
    return "", ""


class LoginTelegramWebAppRequest(BaseModel):
    init_data: str = PydanticField(..., min_length=1)


def _save_telegram_profile(identity, *, first_name=None, last_name=None, username=None, photo_url=None) -> None:
    """Сохраняет имя и фото из проверенных Telegram-данных."""
    first = str(first_name or "").strip()
    last = str(last_name or "").strip()
    display_name = " ".join(part for part in (first, last) if part)[:128]
    clean_username = str(username or "").strip().lstrip("@")[:64]
    clean_photo = str(photo_url or "").strip()

    if display_name:
        identity.display_name = display_name
    if clean_username:
        identity.username = clean_username
    if clean_photo.startswith("https://"):
        identity.avatar_url = clean_photo[:1024]


def _save_telegram_profile_from_mapping(identity, profile: dict) -> None:
    name = profile.get("name") or ""
    first_name = profile.get("first_name") or profile.get("given_name")
    last_name = profile.get("last_name") or profile.get("family_name")
    if not first_name and name:
        parts = str(name).strip().split(maxsplit=1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else None
    _save_telegram_profile(
        identity,
        first_name=first_name,
        last_name=last_name,
        username=profile.get("username") or profile.get("preferred_username"),
        photo_url=profile.get("photo_url") or profile.get("picture"),
    )


@router.post("/login-telegram", response_model=LoginResponse)
async def login_telegram(
    body: LoginTelegramRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    (
        """Вход через Telegram Login Widget (кнопка на сайте). По tg_id находим или создаём Identity, выдаём токен. Срок действия токена: """
        + TOKEN_TTL_HINT
        + "."
    )
    payload = body.model_dump(mode="json")
    if not verify_telegram_login(payload, API_TOKEN, max_age_seconds=TELEGRAM_LOGIN_MAX_AGE):
        raise HTTPException(status_code=401, detail="Неверная подпись или устаревшие данные от Telegram")
    identity = await idb.get_or_create_identity_for_tg(session, body.id)
    _save_telegram_profile(
        identity,
        first_name=body.first_name,
        last_name=body.last_name,
        username=body.username,
        photo_url=body.photo_url,
    )
    await bind_identity_actor(request, session, identity)
    token = await idb.issue_token_for_identity(session, identity, request=request)
    logger.info(
        "[Site:Auth] Вошёл через виджет Telegram: клиент {}, tg {}, ip {}",
        identity.id,
        body.id,
        _client_ip(request),
    )
    set_auth_cookie(response, token, request)
    set_is_admin_cookie(response, identity, request)
    return build_login_response(identity)


@router.post("/login-telegram-webapp", response_model=LoginResponse)
async def login_telegram_webapp(
    body: LoginTelegramWebAppRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Вход через Telegram WebApp initData. Валидирует HMAC, находит/создаёт Identity по tg_id."""
    from utils.telegram_login import verify_webapp_init_data

    result = verify_webapp_init_data(body.init_data, API_TOKEN)
    if not result:
        raise HTTPException(status_code=401, detail="Неверная подпись initData")
    tg_id = result.get("user_id")
    if not tg_id:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя из initData")
    identity = await idb.get_or_create_identity_for_tg(session, int(tg_id))
    try:
        import json

        profile = json.loads(result.get("user_raw") or "{}")
    except (TypeError, ValueError):
        profile = {}
    try:
        profile_id = int(profile.get("id") or 0) if isinstance(profile, dict) else 0
    except (TypeError, ValueError):
        profile_id = 0
    if isinstance(profile, dict) and profile_id == int(tg_id):
        _save_telegram_profile_from_mapping(identity, profile)
    await bind_identity_actor(request, session, identity)
    token = await idb.issue_token_for_identity(session, identity, request=request)
    logger.info(
        "[Site:Auth] Вошёл из Telegram-приложения: клиент {}, tg {}, ip {}",
        identity.id,
        tg_id,
        _client_ip(request),
    )
    set_auth_cookie(response, token, request)
    set_is_admin_cookie(response, identity, request)
    return build_login_response(identity)


class LoginTelegramOIDCRequest(BaseModel):
    code: str = PydanticField(..., min_length=1)
    redirect_uri: str = PydanticField(..., min_length=1)
    code_verifier: str = PydanticField(default="", description="PKCE code_verifier")


async def _resolve_tg_id_from_oidc_code(body: LoginTelegramOIDCRequest) -> tuple[int, dict]:
    """Обменивает код и возвращает Telegram user id с проверенными данными профиля."""
    import base64

    import aiohttp
    import jwt as pyjwt

    client_id, client_secret = _get_oidc_credentials()
    if not client_id or not client_secret:
        logger.warning("[Site:Auth] Вход через Telegram не настроен: нет ключей в настройках")
        raise HTTPException(status_code=503, detail="Вход через Telegram временно недоступен")

    token_data = {
        "grant_type": "authorization_code",
        "code": body.code,
        "redirect_uri": body.redirect_uri,
    }
    if body.code_verifier:
        token_data["code_verifier"] = body.code_verifier

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers = {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"}

    async with aiohttp.ClientSession() as http:
        async with http.post("https://oauth.telegram.org/token", data=token_data, headers=headers) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                logger.warning("[Site:Auth] Telegram не выдал токен, ответ {}: {}", resp.status, err_text[:200])
                raise HTTPException(status_code=401, detail="Не удалось обменять код авторизации")
            token_response = await resp.json()

    id_token = token_response.get("id_token")
    if not id_token:
        raise HTTPException(status_code=401, detail="Telegram не вернул id_token")

    async with aiohttp.ClientSession() as http:
        async with http.get("https://oauth.telegram.org/.well-known/jwks.json") as resp:
            jwks_data = await resp.json()

    try:
        from jwt.api_jwk import PyJWKSet

        jwk_set = PyJWKSet.from_dict(jwks_data)
        unverified_header = pyjwt.get_unverified_header(id_token)
        kid = unverified_header.get("kid")
        key = None
        for k in jwk_set.keys:
            if k.key_id == kid:
                key = k
                break
        if not key:
            raise HTTPException(status_code=401, detail="Ключ подписи не найден в JWKS")

        claims = pyjwt.decode(
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer="https://oauth.telegram.org",
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="ID токен истёк") from None
    except pyjwt.InvalidTokenError as exc:
        logger.warning("[Site:Auth] Telegram прислал негодный токен: {}", exc)
        raise HTTPException(status_code=401, detail="Невалидный ID токен") from exc

    logger.info(
        "[Site:Auth] Telegram передал данные входа: {}",
        {k: v for k, v in claims.items() if k not in ("iat", "exp", "iss", "aud")},
    )

    tg_id = claims.get("id") or claims.get("telegram_id")
    if not tg_id:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя из id_token")

    try:
        tg_id_int = int(tg_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя") from None
    if tg_id_int <= 0 or tg_id_int > 2**53:
        raise HTTPException(status_code=401, detail="Не удалось определить пользователя")
    return tg_id_int, claims


@router.post("/login-telegram-oidc", response_model=LoginResponse)
async def login_telegram_oidc(
    body: LoginTelegramOIDCRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Вход через Telegram OIDC (authorization code flow). Обменивает code на id_token, верифицирует JWT."""
    tg_id_int, profile = await _resolve_tg_id_from_oidc_code(body)

    identity = await idb.get_or_create_identity_for_tg(session, tg_id_int)
    _save_telegram_profile_from_mapping(identity, profile)
    await bind_identity_actor(request, session, identity)
    token = await idb.issue_token_for_identity(session, identity, request=request)

    if getattr(identity, "is_admin", False):
        from database.site_state import mark_site_initialized

        await mark_site_initialized(session)

    logger.info(
        "[Site:Auth] Вошёл через Telegram: клиент {}, tg {}, ip {}",
        identity.id,
        tg_id_int,
        _client_ip(request),
    )
    set_auth_cookie(response, token, request)
    set_is_admin_cookie(response, identity, request)
    return build_login_response(identity)


@router.post("/link-telegram-oidc", response_model=IdentityResponse)
async def link_telegram_oidc(
    body: LoginTelegramOIDCRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Привязывает Telegram к текущей identity через OIDC."""
    if identity.tg_id is not None:
        raise HTTPException(status_code=409, detail="Telegram уже привязан к этому аккаунту")

    tg_id_int, profile = await _resolve_tg_id_from_oidc_code(body)
    result = await idb.attach_telegram(session, identity.id, tg_id_int)
    if not result:
        raise HTTPException(
            status_code=409,
            detail="Этот Telegram уже привязан к другой идентичности",
        )
    _save_telegram_profile_from_mapping(result, profile)
    await bind_identity_actor(request, session, result)
    set_is_admin_cookie(response, result, request)
    try:
        from services.panel_identity_sync import push_identity_to_panel

        await push_identity_to_panel(session, tg_id_int)
    except Exception as e:
        logger.debug("[Site:Auth] Не удалось синхронизировать клиента с панелью: {}", e)
    logger.info(
        "[Site:Auth] Telegram привязан к аккаунту: клиент {}, tg {}, ip {}",
        result.id,
        tg_id_int,
        _client_ip(request),
    )
    return IdentityResponse.model_validate(result)


@router.post("/link-telegram", response_model=IdentityResponse)
async def link_telegram(
    body: LinkTelegramRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    identity=Depends(verify_identity_token),
):
    """Привязывает Telegram к текущей идентичности. Требуется подпись от Telegram Login Widget (доказательство владения аккаунтом)."""
    if identity.tg_id is not None:
        raise HTTPException(status_code=409, detail="Telegram уже привязан к этому аккаунту")
    payload = body.model_dump(mode="json")
    if not verify_telegram_login(payload, API_TOKEN, max_age_seconds=TELEGRAM_LOGIN_MAX_AGE):
        raise HTTPException(status_code=401, detail="Неверная подпись или устаревшие данные от Telegram")
    result = await idb.attach_telegram(session, identity.id, body.id)
    if not result:
        raise HTTPException(
            status_code=409,
            detail="Этот Telegram уже привязан к другой идентичности",
        )
    _save_telegram_profile(
        result,
        first_name=body.first_name,
        last_name=body.last_name,
        username=body.username,
        photo_url=body.photo_url,
    )
    await bind_identity_actor(request, session, result)
    set_is_admin_cookie(response, result, request)
    try:
        from services.panel_identity_sync import push_identity_to_panel

        await push_identity_to_panel(session, int(body.id))
    except Exception as e:
        logger.debug("[Site:Auth] Не удалось синхронизировать клиента с панелью: {}", e)
    return IdentityResponse.model_validate(result)
