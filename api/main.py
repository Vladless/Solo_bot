import asyncio
import hashlib
import os

from time import perf_counter

from fastapi import Depends, FastAPI, Request
from fastapi.responses import ORJSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response as StarletteResponse
from starlette.staticfiles import StaticFiles

from audit import ensure_api_context, log_api_access, record_api_access_event_background
from database import async_session_maker
from logger import logger
from settings.config import API_CORS_ORIGINS, API_LOGGING, API_VERSION, LOGGING_LEVEL


if API_VERSION == 1:
    from api.v1 import (
        VERSION as API_DOC_VERSION,
        router as api_router,
    )
else:
    from api.v2 import VERSION as API_DOC_VERSION
    from api.v2.router import router as api_router

_docs_enabled = str(LOGGING_LEVEL or "").upper() == "DEBUG" or os.getenv("API_DOCS", "").strip() == "1"

app = FastAPI(
    title=f"SoloBot API (Alpha) — API v{API_DOC_VERSION}",
    version=API_DOC_VERSION,
    description=f"Версия API: **v{API_DOC_VERSION}**.",
    docs_url="/api/docs" if _docs_enabled else None,
    redoc_url="/api/redoc" if _docs_enabled else None,
    openapi_url="/api/openapi.json" if _docs_enabled else None,
    default_response_class=ORJSONResponse,
)

_cors_origins = API_CORS_ORIGINS if API_CORS_ORIGINS != ["*"] else API_CORS_ORIGINS
_cors_credentials = API_CORS_ORIGINS != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["X-Identity-Id", "X-Token", "Content-Type", "Authorization"],
)

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

_shutting_down = False


class QuietShutdownMiddleware:
    """Гасит отмену запроса после сигнала остановки."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except asyncio.CancelledError:
            if _shutting_down:
                return
            raise


@app.on_event("shutdown")
async def _mark_shutting_down() -> None:
    global _shutting_down
    _shutting_down = True


_WEAK_PASSWORDS = frozenset({"111", "1111", "admin", "password", "12345678", "qwerty"})


def _log_security_checklist() -> None:
    from settings import config

    api_token_ttl_days = getattr(config, "API_TOKEN_TTL_DAYS", None)
    logging_level = getattr(config, "LOGGING_LEVEL", "INFO")
    web_admin_password = getattr(config, "WEB_ADMIN_PASSWORD", None)

    issues: list[str] = []
    pwd = (web_admin_password or "").strip()
    if pwd and (len(pwd) < 8 or pwd.lower() in _WEAK_PASSWORDS):
        issues.append("WEB_ADMIN_PASSWORD слабый (короче 8 символов или словарный) — смените в config.py")
    if API_CORS_ORIGINS == ["*"]:
        issues.append("API_CORS_ORIGINS = ['*'] — укажите конкретные домены в config.py")
    if str(logging_level).upper() == "DEBUG":
        issues.append("LOGGING_LEVEL = DEBUG — в проде используйте INFO или WARNING")
    if api_token_ttl_days is None:
        issues.append("API_TOKEN_TTL_DAYS не задан — сессии бессрочные, рекомендуется 30-90 дней")
    for issue in issues:
        logger.warning("[SECURITY] {}", issue)
    if issues:
        logger.warning("[SECURITY] Найдено проблем конфигурации: {}. Не выходите в прод, не устранив их.", len(issues))


_log_security_checklist()


@app.exception_handler(Exception)
async def _generic_exception_handler(request: Request, exc: Exception):
    from audit import ensure_api_context

    context = ensure_api_context(request)
    logger.exception("[API] Unhandled exception at {} {}: {}", request.method, request.url.path, exc)
    return ORJSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера", "request_id": context.request_id},
    )


_ETAG_MAX_BODY_BYTES = 256 * 1024


@app.middleware("http")
async def security_and_cache_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")

    content_type = response.headers.get("content-type", "")
    path = request.url.path

    if path.startswith("/api/web/uploads/") and request.method == "GET" and response.status_code == 200:
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        if path.lower().endswith(".svg"):
            response.headers["Content-Disposition"] = "attachment"
        return response

    if request.method == "GET" and response.status_code == 200 and "application/json" in content_type:
        content_length_header = response.headers.get("content-length")
        try:
            cl = int(content_length_header) if content_length_header is not None else None
        except (TypeError, ValueError):
            cl = None
        if cl is not None and cl > _ETAG_MAX_BODY_BYTES:
            response.headers.setdefault("Cache-Control", "no-cache")
            return response
        chunks: list[bytes] = []
        total = 0
        too_big = False
        async for chunk in response.body_iterator:
            total += len(chunk)
            if total > _ETAG_MAX_BODY_BYTES:
                chunks.append(chunk)
                too_big = True
                async for remaining in response.body_iterator:
                    chunks.append(remaining)
                break
            chunks.append(chunk)
        body = b"".join(chunks)
        if too_big:
            headers = dict(response.headers)
            headers["Cache-Control"] = "no-cache"
            headers.pop("content-length", None)
            return StarletteResponse(content=body, status_code=200, headers=headers, media_type=response.media_type)
        etag = '"' + hashlib.md5(body).hexdigest() + '"'
        if_none_match = request.headers.get("if-none-match", "")
        client_etags = [t.strip() for t in if_none_match.split(",") if t.strip()]
        if etag in client_etags or if_none_match.strip() == "*":
            return StarletteResponse(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        headers = dict(response.headers)
        headers["ETag"] = etag
        headers["Cache-Control"] = "no-cache"
        return StarletteResponse(content=body, status_code=200, headers=headers, media_type=response.media_type)

    response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.middleware("http")
async def api_access_log_middleware(request: Request, call_next):
    context = ensure_api_context(request)
    started = perf_counter()

    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = int((perf_counter() - started) * 1000)
        if API_LOGGING:
            logger.opt(exception=exc).error(
                f"[API] {request.method} {request.url.path} → 500 ({type(exc).__name__})"
            )
            log_api_access(
                request,
                status_code=500,
                duration_ms=duration_ms,
                result="fail",
                reason=type(exc).__name__,
            )
        asyncio.create_task(
            record_api_access_event_background(
                async_session_maker,
                request,
                result="fail",
                reason=type(exc).__name__,
                status_code=500,
            )
        )
        raise

    duration_ms = int((perf_counter() - started) * 1000)
    response.headers["X-Request-Id"] = context.request_id
    response.headers["X-Response-Time"] = f"{duration_ms}ms"
    result = "success" if response.status_code < 400 else "fail"
    if API_LOGGING:
        log_api_access(
            request,
            status_code=response.status_code,
            duration_ms=duration_ms,
            result=result,
        )
    asyncio.create_task(
        record_api_access_event_background(
            async_session_maker,
            request,
            result=result,
            reason=None if response.status_code < 400 else str(response.status_code),
            status_code=response.status_code,
        )
    )
    return response


app.add_middleware(QuietShutdownMiddleware)


@app.get("/api/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


from api.depends import (
    get_session as _get_session,
    verify_identity_admin as _verify_admin,
)


@app.get("/api/health/detailed", include_in_schema=False)
async def health_detailed(
    session: AsyncSession = Depends(_get_session),
    _identity=Depends(_verify_admin),
):
    import time

    from sqlalchemy import text

    from core.redis_cache import _get_redis

    checks: dict[str, object] = {"status": "ok", "timestamp": int(time.time())}

    try:
        await session.execute(text("SELECT 1"))
        checks["db"] = {"ok": True}
    except Exception as e:
        checks["db"] = {"ok": False, "error": str(e)[:200]}
        checks["status"] = "degraded"

    try:
        client = await _get_redis()
        if client is not None:
            await client.ping()
            checks["redis"] = {"ok": True}
        else:
            checks["redis"] = {"ok": False, "error": "unavailable"}
            checks["status"] = "degraded"
    except Exception as e:
        checks["redis"] = {"ok": False, "error": str(e)[:200]}
        checks["status"] = "degraded"

    return checks


app.include_router(api_router)

_web_uploads_dir = "static/web_uploads"
os.makedirs(_web_uploads_dir, exist_ok=True)
app.mount("/api/web/uploads", StaticFiles(directory=_web_uploads_dir), name="web_uploads")

_web_packs_dir = "static/web_packs"
os.makedirs(_web_packs_dir, exist_ok=True)
app.mount("/api/web/packs/files", StaticFiles(directory=_web_packs_dir), name="web_packs")
