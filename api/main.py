from time import perf_counter

from fastapi import FastAPI, Request
from api.routes import (
    users,
    keys,
    coupons,
    servers,
    tariffs,
    gifts,
    referrals,
    misc,
    partners,
    modules,
    management,
    settings,
)
from config import API_LOGGING
from logger import logger

app = FastAPI(
    title="SoloBot API (Alpha)",
    version="0.5.2",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)


@app.middleware("http")
async def api_access_log_middleware(request: Request, call_next):
    if not API_LOGGING:
        return await call_next(request)

    started = perf_counter()
    response = await call_next(request)
    duration_ms = int((perf_counter() - started) * 1000)
    client_ip = request.client.host if request.client else "-"
    path_qs = request.url.path
    if request.url.query:
        path_qs = f"{path_qs}?{request.url.query}"

    logger.info(
        f'[API] {client_ip} "{request.method} {path_qs}" {response.status_code} {duration_ms}ms'
    )
    return response

app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(keys.router, prefix="/api/keys", tags=["Keys"])
app.include_router(coupons.router, prefix="/api/coupons", tags=["Coupons"])
app.include_router(servers.router, prefix="/api/servers", tags=["Servers"])
app.include_router(tariffs.router, prefix="/api/tariffs", tags=["Tariffs"])
app.include_router(gifts.router, prefix="/api/gifts", tags=["Gifts"])
app.include_router(referrals.router, prefix="/api/referrals", tags=["Referrals"])
app.include_router(partners.router, prefix="/api/partners", tags=["Partners"])
app.include_router(misc.router, prefix="/api")
app.include_router(modules.router, prefix="/api")
app.include_router(management.router, prefix="/api/management", tags=["Management"])
app.include_router(settings.router, prefix="/api/settings", tags=["Settings"])


@app.get("/api", include_in_schema=False)
async def root():
    return {"message": "Welcome to SoloBot API. Docs: /api/docs"}
